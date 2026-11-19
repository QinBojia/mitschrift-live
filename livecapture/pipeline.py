"""Capture -> echo handling -> speech recognition -> translation, as one restartable unit.

Used by the desktop backend (server.py). Models are expensive to load, so they live in a
ModelCache that survives pipeline restarts (device or option changes).

Either side (system audio "other", microphone "me") can be switched off and on while the
pipeline runs (set_sources): a side that is off is not recognized, shown or recorded, and
its device is closed - except the loopback, which stays open as the reference of the echo
canceller while the microphone is on (with speakers, the mic would otherwise hear the
other side and show it as "me").
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
from .i18n import tr
from .segmenter import Caption, SegmenterConfig, StreamProcessor
from .sensevoice import SenseVoice
from .stt import SttEngine
from .translate import DEFAULT_SCENE, TranslationUnavailable, Translator, make_backend
from .vad import SileroVad

STREAMS = ("other", "me")  # system audio (loopback), microphone


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
    # interface language: auto (follow Windows) | zh | en; default from the edition (friends: zh)
    ui_lang: str = field(default_factory=lambda: EDITION.default_ui_lang)

    RESTART_KEYS = ("engine", "aec", "echo_filter", "zh", "speaker", "mic", "min_silence",
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
        self.sources = [k for k in STREAMS if k in (self.sources or [])]
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
        raise TranslationUnavailable(tr("mt.unavailable"))


class Pipeline:
    def __init__(self, settings: Settings, cache: ModelCache, on_caption: Callable[[Caption], None],
                 on_level: Callable[[str, float], None] | None = None,
                 on_audio: Callable[[str, object], None] | None = None):
        self.settings, self.cache = settings, cache
        self.on_caption, self.on_level, self.on_audio = on_caption, on_level, on_audio
        self.engine: SttEngine | None = None
        self.translator: Translator | None = None
        self.echo_filter: EchoFilter | None = None
        self.sources: dict = {}  # open devices (or files) by stream
        self.procs: dict[str, StreamProcessor] = {}  # the sides that are on: recognized, shown, recorded
        self._system: AudioSystem | None = None
        self._aec: EchoCanceller | None = None
        self._lock = threading.Lock()  # start / stop / set_sources
        self._live = True
        self.running = False

    # ---- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        s = self.settings
        self.engine = self.cache.stt(s.engine)
        self._sv = self.cache.sensevoice() if s.zh else None
        self._vad_path = self.cache.vad_path()
        self._live = not s.file
        self._cfg = SegmenterConfig(min_silence=s.min_silence, max_segment=min(12.0, self.engine.max_seconds - 1.0))

        self.translator = Translator(self.on_caption, _NoBackend(), s.target if s.target else "off")
        self.set_model(s.model, s.scene)
        # hides a mic caption that repeats the other side: idle while only one side is on
        self.echo_filter = EchoFilter(self.translator.on_caption) if s.echo_filter else None
        self._first = self.echo_filter.on_caption if self.echo_filter else self.translator.on_caption

        with self._lock:
            self.running = True
            if self._live:
                self._system = AudioSystem()
                self.sources = {}
                self._apply(set(s.sources))
            else:  # replay: the files decide which sides there are
                self.sources = {"other": FileSource(s.file, s.speed)}
                if s.file_me:
                    self.sources["me"] = FileSource(s.file_me, s.speed)
                self._aec = EchoCanceller() if len(self.sources) == 2 and s.aec else None
                for k in self.sources:
                    self._add_proc(k)
                for k, src in self.sources.items():
                    src.start(self._feeds[k])

    def set_sources(self, enabled: list[str]) -> None:
        """Switch sides on or off while running (live capture; a replay keeps its files)."""
        with self._lock:
            if self.running and self._live:
                self._apply(set(enabled))

    def _apply(self, want: set[str]) -> None:
        """Bring processors and devices in line with the sides that should be on."""
        s = self.settings
        mic = "me" in want
        # the loopback is also the reference of the echo canceller for the microphone
        need = {"other": "other" in want or (mic and s.aec), "me": mic}
        for k in [k for k in self.procs if k not in want]:
            self._drop_proc(k)  # what it was hearing still comes out as a last caption
        for k in [k for k in self.sources if not need[k]]:
            self._close_source(k)
        if not mic:
            self._aec = None
        elif s.aec and self._aec is None:
            self._aec = EchoCanceller()
        for k in STREAMS:
            if k in want and k not in self.procs:
                self._add_proc(k)
        failed = None
        for k, kind, device in (("other", "loopback", s.speaker), ("me", "mic", s.mic)):
            if need[k] and k not in self.sources:
                try:
                    src = LiveSource(self._system, kind, device or None)
                    src.start(self._feeds[k])
                    self.sources[k] = src
                except Exception as e:  # noqa: BLE001 - e.g. no microphone: the other side still runs
                    self._drop_proc(k)
                    failed = failed or e
        if failed:
            raise failed

    def _add_proc(self, k: str) -> None:
        cfg = self._cfg
        if k == "me" and self._aec:  # double-talk leaves dips in the cleaned mic signal (bench/echo_eval.py)
            cfg = dataclasses.replace(cfg, min_silence=max(cfg.min_silence, 0.8))
        p = StreamProcessor(k, self.engine, SileroVad(self._vad_path), cfg, self._first, live=self._live,
                            on_level=self.on_level, sensevoice=self._sv)
        p.start()
        self.procs[k] = p

    def _drop_proc(self, k: str) -> None:
        p = self.procs.pop(k, None)
        if p:
            p.stop()
            p.join(timeout=10)

    def _close_source(self, k: str) -> None:
        src = self.sources.pop(k, None)
        try:
            if src:
                src.stop()
        except Exception:  # noqa: BLE001 - device may already be gone
            pass

    # ---- audio in (capture threads) --------------------------------------------
    @property
    def _feeds(self) -> dict:
        return {"other": self._feed_other, "me": self._feed_me}

    def _feed_other(self, block) -> None:
        aec = self._aec
        if aec:
            aec.push_reference(block)
        self._deliver("other", block)

    def _feed_me(self, block) -> None:
        aec = self._aec
        if aec:
            block = aec.process_capture(block)
            if not len(block):
                return
        self._deliver("me", block)

    def _deliver(self, k: str, block) -> None:
        p = self.procs.get(k)
        if p is None:  # this side is off (the loopback may still run as the echo reference)
            return
        if self.on_audio:  # the recorder: here, at capture time, not when the recognizer gets to it
            self.on_audio(k, block)
        p.feed(block)

    def stop(self) -> None:
        with self._lock:
            for src in self.sources.values():
                try:
                    src.stop()
                except Exception:  # noqa: BLE001 - device may already be gone
                    pass
            procs = list(self.procs.values())
            for p in procs:
                p.stop()
            for p in procs:
                p.join(timeout=10)
            self._aec = None
            self.running = False
        if self.translator:
            self.translator.flush(8)
        if self._system:
            self._system.close()
            self._system = None

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
            "lag": max((p.lag for p in list(self.procs.values())), default=0.0),
        }
