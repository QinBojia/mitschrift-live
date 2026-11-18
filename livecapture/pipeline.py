"""Capture -> echo handling -> speech recognition -> translation, as one restartable unit.

Used by the desktop backend (server.py). Models are expensive to load, so they live in a
ModelCache that survives pipeline restarts (device or option changes).
"""

from __future__ import annotations

import dataclasses
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import models
from .audio import AudioSystem, FileSource, LiveSource
from .edition import EDITION
from .echo import EchoCanceller, EchoFilter
from .segmenter import Caption, SegmenterConfig, StreamProcessor
from .sensevoice import SenseVoice
from .stt import SttEngine
from .translate import DEFAULT_SCENE, TranslationUnavailable, Translator, make_backend
from .vad import SileroVad


@dataclass
class Settings:
    engine: str = "cuda"  # cuda | npu | dml | cpu
    target: str = "zh"  # zh | en | off
    model: str = "deepseek-flash"
    scene: str = DEFAULT_SCENE
    aec: bool = True
    echo_filter: bool = True
    zh: bool = True  # Chinese detection / SenseVoice
    speaker: str = ""  # loopback device name ("" = Windows default output)
    mic: str = ""  # microphone name ("" = Windows default input)
    sources: list[str] = field(default_factory=lambda: ["other", "me"])
    min_silence: float = 0.5
    file: str = ""  # debug: replay this file as "other" instead of live capture
    file_me: str = ""  # debug: replay this file as "me"
    speed: float = 1.0  # replay speed for file/file_me

    RESTART_KEYS = ("engine", "aec", "echo_filter", "zh", "speaker", "mic", "sources", "min_silence",
                    "file", "file_me", "speed")

    @classmethod
    def load(cls, path: Path) -> Settings:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls().constrained()
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known}).constrained()

    def constrained(self) -> Settings:
        """Apply the build edition's limits (e.g. the friends' build: CPU only, DeepSeek only)."""
        if EDITION.engines and self.engine not in EDITION.engines:
            self.engine = EDITION.engine or EDITION.engines[0]
        if EDITION.models and self.model not in EDITION.models:
            self.model = EDITION.default_model or EDITION.models[0]
        return self

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dataclasses.asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


class ModelCache:
    """Loads each model once per process; thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stt: dict[str, SttEngine] = {}
        self._sv: SenseVoice | None = None
        self._vad: Path | None = None

    def stt(self, kind: str) -> SttEngine:
        with self._lock:
            if kind not in self._stt:
                self._stt[kind] = SttEngine(kind)
            return self._stt[kind]

    def sensevoice(self) -> SenseVoice:
        with self._lock:
            if self._sv is None:
                self._sv = SenseVoice(models.ensure_sensevoice())
            return self._sv

    def vad_path(self) -> Path:
        with self._lock:
            if self._vad is None:
                self._vad = models.ensure_silero()
            return self._vad


class _NoBackend:
    model = "-"

    def __call__(self, *args):
        raise TranslationUnavailable("翻译不可用")


class Pipeline:
    def __init__(self, settings: Settings, cache: ModelCache, on_caption: Callable[[Caption], None],
                 on_level: Callable[[str, float], None] | None = None,
                 on_audio: Callable[[str, object], None] | None = None):
        self.settings, self.cache = settings, cache
        self.on_caption, self.on_level, self.on_audio = on_caption, on_level, on_audio
        self.engine: SttEngine | None = None
        self.translator: Translator | None = None
        self.echo_filter: EchoFilter | None = None
        self.sources: dict = {}
        self.procs: dict[str, StreamProcessor] = {}
        self._system: AudioSystem | None = None
        self.running = False

    # ---- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        s = self.settings
        self.engine = self.cache.stt(s.engine)
        sv = self.cache.sensevoice() if s.zh else None
        vad_path = self.cache.vad_path()

        live = not s.file
        if live:
            self._system = AudioSystem()
            self.sources = {}
            if "other" in s.sources:
                self.sources["other"] = LiveSource(self._system, "loopback", s.speaker or None)
            if "me" in s.sources:
                self.sources["me"] = LiveSource(self._system, "mic", s.mic or None)
        else:
            self.sources = {"other": FileSource(s.file, s.speed)}
            if s.file_me:
                self.sources["me"] = FileSource(s.file_me, s.speed)

        self.translator = Translator(self.on_caption, _NoBackend(), s.target if s.target else "off")
        self.set_model(s.model, s.scene)

        both = "other" in self.sources and "me" in self.sources
        aec = EchoCanceller() if both and s.aec else None
        self.echo_filter = EchoFilter(self.translator.on_caption) if both and s.echo_filter else None
        first = self.echo_filter.on_caption if self.echo_filter else self.translator.on_caption

        cfg = SegmenterConfig(min_silence=s.min_silence, max_segment=min(12.0, self.engine.max_seconds - 1.0))
        cfgs = {k: cfg for k in self.sources}
        if aec:  # double-talk leaves dips in the cleaned mic signal (bench/echo_eval.py)
            cfgs["me"] = dataclasses.replace(cfg, min_silence=max(cfg.min_silence, 0.8))
        self.procs = {
            k: StreamProcessor(k, self.engine, SileroVad(vad_path), cfgs[k], first, live=live,
                               on_level=self.on_level, on_audio=self.on_audio, sensevoice=sv)
            for k in self.sources
        }
        for p in self.procs.values():
            p.start()
        feeds = {k: self.procs[k].feed for k in self.sources}
        if aec:
            other_feed, me_feed = feeds["other"], feeds["me"]

            def feed_other(block, _f=other_feed) -> None:
                aec.push_reference(block)
                _f(block)

            def feed_me(block, _f=me_feed) -> None:
                cleaned = aec.process_capture(block)
                if len(cleaned):
                    _f(cleaned)

            feeds = {"other": feed_other, "me": feed_me}
        for k, src in self.sources.items():
            src.start(feeds[k])
        self.running = True

    def stop(self) -> None:
        for src in self.sources.values():
            try:
                src.stop()
            except Exception:  # noqa: BLE001 - device may already be gone
                pass
        for p in self.procs.values():
            p.stop()
        for p in self.procs.values():
            p.join(timeout=10)
        if self.translator:
            self.translator.flush(8)
        if self._system:
            self._system.close()
            self._system = None
        self.running = False

    @property
    def files_done(self) -> bool:
        """Replay mode: all files have been fed."""
        return all(getattr(src, "done", None) is not None and src.done.is_set() for src in self.sources.values())

    # ---- live settings -------------------------------------------------------
    def set_target(self, target: str) -> None:
        if self.translator:
            self.translator.set_target(target)

    def set_model(self, model: str, scene: str) -> str | None:
        """Swap the translation backend; returns an error message if it can't be used."""
        if not self.translator:
            return None
        try:
            self.translator.set_backend(make_backend(model, scene))
            return None
        except Exception as e:  # noqa: BLE001 - missing key, unknown model, ...
            self.translator.set_backend(_NoBackend())
            self.translator.disabled, self.translator.error = True, str(e)
            return str(e)

    # ---- status --------------------------------------------------------------
    def describe_sources(self) -> dict[str, str]:
        """Short device names for the status bar."""
        return {k: src.name.replace(" [Loopback]", "") for k, src in self.sources.items()}

    def stats(self) -> dict:
        asr = self.engine.stats() if self.engine else None
        sv = self.cache._sv if self.settings.zh else None  # noqa: SLF001 - read-only peek, no loading
        zh = sv.stats() if sv else None
        mt = self.translator.stats() if self.translator else None
        return {
            "asr_ms": round(asr[0]) if asr else None,
            "asr_p95_ms": round(asr[1]) if asr else None,
            "asr_calls": self.engine.calls if self.engine else 0,
            "zh_ms": round(zh[0]) if zh else None,
            "zh_calls": sv.calls if sv else 0,
            "mt_ms": round(mt[0]) if mt else None,
            "mt_error": self.translator.error if self.translator else None,
            "lag": max((p.lag for p in self.procs.values()), default=0.0),
        }
