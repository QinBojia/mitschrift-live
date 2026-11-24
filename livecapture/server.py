"""Backend for the desktop UI: runs the Pipeline and talks to the pages over a WebSocket.

Protocol (see ui/client.js):
  server -> page: caption / status / levels / devices / clear
  page -> server: {type:"control", action:"start"|"stop"}, {type:"set", key, value}, {type:"clear"}

The backend keeps the finished captions since launch (or since the user cleared them): a
recording started later begins with them, so what was said before pressing ● Record is in it
too (with negative offsets: there is no audio for it). A recording runs from ● Record on to off
(or quitting): pausing and the automatic restarts (another default device, a changed engine)
don't split it - its audio is filled with silence meanwhile and meta.json notes the pause.

The socket listens on 127.0.0.1 only, on a random port, and needs a random token from
the page URL - a web page from anywhere else can't connect and read the captions.
Start/stop/settings run on one worker thread so model loading never blocks the socket.
"""

from __future__ import annotations

import asyncio
import dataclasses
import http
import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve

from . import __version__, appaudio, i18n, keys, models
from .i18n import tr
from .audio import AudioSystem
from .edition import EDITION
from .pipeline import STREAMS, ModelCache, Pipeline, Settings
from .recorder import DEFAULT_DIR as DEFAULT_RECORD_DIR
from .recorder import Recorder
from .segmenter import Caption
from .translate import detect_de_en

SETTINGS_FILE = models.APP_DIR / "settings.json"
HISTORY_MAX = 20000  # finished captions kept for a recording started later (~20 h of talk)


def engine_label(engine: str) -> str:
    return tr(f"engine.{engine}") if engine in ("cuda", "npu", "dml", "cpu") else engine

_last_times: tuple[int, int] | None = None  # (idle, total) from the previous _cpu_percent() call


def _cpu_percent() -> int | None:
    """Whole-machine CPU use since the previous call (GetSystemTimes; no psutil in the build)."""
    global _last_times
    import ctypes
    from ctypes import wintypes

    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    val = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime  # noqa: E731
    now = (val(idle), val(kernel) + val(user))  # kernel time includes idle time
    prev, _last_times = _last_times, now
    if prev is None or now[1] == prev[1]:
        return None
    return round(100 * (1 - (now[0] - prev[0]) / (now[1] - prev[1])))


class Backend:
    def __init__(self, settings_path: Path = SETTINGS_FILE):
        self.settings_path = settings_path
        self.settings = Settings.load(settings_path)
        i18n.set_lang(self.settings.ui_lang)
        self.cache = ModelCache()
        self.pipeline: Pipeline | None = None
        self.token = secrets.token_urlsafe(18)
        self.listeners: list = []  # callables getting every broadcast message (dict), any thread
        self._key_status = keys.status()  # where each translation key comes from (no key material)
        self.port: int | None = None
        self.loading: str | None = None
        self.error: str | None = None
        self.session = 0
        self.levels = {"other": -100.0, "me": -100.0}
        # recording: a per-launch toggle (never saved, so nothing is recorded by surprise)
        self.record_on = False
        self.record_dir = Path(self.settings.record_dir) if self.settings.record_dir else DEFAULT_RECORD_DIR
        self.recorder: Recorder | None = None
        self.last_recording: str | None = None
        self.history: list[dict] = []  # finished captions since launch / the last clear (see above)
        self._hist_lock = threading.Lock()  # history + recorder: no caption lost or doubled at ● Record
        self._apps: list[dict] = []  # apps with an audio session (appaudio.list_apps), from the device watch
        self.notice: str | None = None  # short info line for the UI (e.g. device switched)
        self._notice_until = 0.0
        self._clients: set[ServerConnection] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ctl = ThreadPoolExecutor(1, thread_name_prefix="backend-ctl")
        self._stopping = threading.Event()

    # ---- socket server ---------------------------------------------------------
    def serve(self, ui_origin: str | None = None) -> int:
        """Start the WebSocket server in a background thread; returns its port."""
        ready = threading.Event()

        def run() -> None:
            async def main() -> None:
                origins = [ui_origin] if ui_origin else None
                async with serve(self._handler, "127.0.0.1", 0, origins=origins,
                                 process_request=self._check_token) as server:
                    self.port = server.sockets[0].getsockname()[1]
                    self._loop = asyncio.get_running_loop()
                    ready.set()
                    asyncio.create_task(self._tick())
                    while not self._stopping.is_set():
                        await asyncio.sleep(0.2)

            asyncio.run(main())

        threading.Thread(target=run, daemon=True, name="backend-ws").start()
        threading.Thread(target=self._watch_default_devices, daemon=True, name="device-watch").start()
        ready.wait(10)
        return self.port

    # ---- follow the Windows default devices ------------------------------------------
    def _watch_default_devices(self) -> None:
        """Every 2 s: if the Windows default output/input changed (headphones plugged in,
        a USB mic, ...), restart capture on the new device for streams set to "default".
        Also keeps the list of apps that use sound (for "Them") current in the dropdown."""
        try:
            import comtypes
            from pycaw.constants import EDataFlow, ERole
            from pycaw.pycaw import AudioUtilities

            comtypes.CoInitialize()
        except Exception:  # noqa: BLE001 - no pycaw: just don't follow
            return
        last = None
        while True:
            if appaudio.supported():
                try:
                    apps = appaudio.list_apps()
                except Exception:  # noqa: BLE001 - keep the last list
                    apps = self._apps
                if apps != self._apps:
                    self._apps = apps
                    self._ctl.submit(self._send_devices)
            if self._stopping.wait(2.0):
                return
            try:
                e = AudioUtilities.GetDeviceEnumerator()
                names = tuple(
                    AudioUtilities.CreateDevice(e.GetDefaultAudioEndpoint(flow.value, ERole.eConsole.value)).FriendlyName
                    for flow in (EDataFlow.eRender, EDataFlow.eCapture)
                )
            except Exception:  # noqa: BLE001 - e.g. no device of one kind right now
                continue
            if last is not None and names != last:
                self._ctl.submit(self._on_default_devices_changed, *names)
            last = names

    def _on_default_devices_changed(self, speaker: str, mic: str) -> None:
        self._send_devices()  # the dropdown lists changed too
        p, s = self.pipeline, self.settings
        if not (p and p.running) or s.file:
            return
        current = p.describe_sources()
        moved = []
        if "other" in current and not s.speaker and current["other"] != speaker:
            moved.append(f"{tr('who.other')} → {speaker}")
        if "me" in current and not s.mic and current["me"] != mic:
            moved.append(f"{tr('who.me')} → {mic}")
        if moved:
            self.stop()
            self.start()
            self._notify(tr("devices.switched", list=", ".join(moved)))

    def _notify(self, text: str, secs: float = 8.0) -> None:
        self.notice = text
        self._notice_until = time.time() + secs
        self.broadcast(self._status())

    def _check_token(self, connection, request):
        if not request.path.startswith("/ws") or f"token={self.token}" not in request.path:
            return connection.respond(http.HTTPStatus.FORBIDDEN, "forbidden\n")
        return None

    async def _handler(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        try:
            await ws.send(json.dumps(self._status(), ensure_ascii=False))
            self._ctl.submit(self._send_devices)
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                self._ctl.submit(self._handle, msg)
        finally:
            self._clients.discard(ws)

    def broadcast(self, obj: dict) -> None:
        for listener in self.listeners:  # in-process consumers (the native caption bar)
            try:
                listener(obj)
            except Exception as e:  # noqa: BLE001 - a broken listener must not stop the captions
                print(f"listener failed: {type(e).__name__}: {e}", flush=True)
        loop = self._loop
        if loop is None or not self._clients:
            return
        data = json.dumps(obj, ensure_ascii=False)
        loop.call_soon_threadsafe(self._send_all, data)

    def _send_all(self, data: str) -> None:  # runs on the event loop
        for ws in list(self._clients):
            asyncio.ensure_future(self._safe_send(ws, data))

    @staticmethod
    async def _safe_send(ws: ServerConnection, data: str) -> None:
        try:
            await ws.send(data)
        except Exception:  # noqa: BLE001 - client went away
            pass

    async def _tick(self) -> None:
        n = 0
        while not self._stopping.is_set():
            await asyncio.sleep(0.1)
            n += 1
            if self.pipeline and self.pipeline.running:
                self._send_all(json.dumps({"type": "levels", **self.levels}))
                for k in self.levels:  # decay so a source that goes quiet drops back
                    self.levels[k] = max(-100.0, self.levels[k] - 3)
            if n % 10 == 0:
                self._send_all(json.dumps(self._status(), ensure_ascii=False))
                self._check_replay_done()
            if n % 600 == 0 and self.pipeline and self.pipeline.running:
                self._log_stats()

    def _log_stats(self) -> None:
        """Once a minute while running: speed figures for the log (remote diagnosis on friends' PCs).

        asr = Parakeet (de/en, on the chosen engine), zh = SenseVoice (Chinese + language ID, CPU);
        n = calls in the last minute, ms = mean over the last 200 calls.
        """
        st = self.pipeline.stats()
        prev = getattr(self, "_prev_calls", (0, 0))
        calls = (st.get("asr_calls", 0), st.get("zh_calls", 0))
        n_asr, n_zh = (max(0, c - p) for c, p in zip(calls, prev, strict=True))  # restart resets counters
        self._prev_calls = calls
        print(f"[stats] engine={self.settings.engine} asr n={n_asr} ms={st.get('asr_ms')} p95={st.get('asr_p95_ms')} "
              f"| zh n={n_zh} ms={st.get('zh_ms')} | mt_ms={st.get('mt_ms')} lag={st.get('lag', 0.0):.1f}s "
              f"cpu={_cpu_percent()}%", flush=True)

    # ---- pipeline callbacks ------------------------------------------------------
    def _on_audio(self, stream: str, block) -> None:
        r = self.recorder
        if r:
            r.on_audio(stream, block)

    def _on_caption(self, c: Caption) -> None:
        src = "zh" if c.lang == "zh" else (detect_de_en(c.text) if c.text else None)
        # microphone alone: it is listening to the room (a lecture), not to "me"
        who = "room" if c.stream == "me" and "other" not in self.settings.sources and not self.settings.file else None
        if c.final and c.committed:
            record = {**dataclasses.asdict(c), "session": self.session, "who": who, "src": src}
            with self._hist_lock:
                self.history.append(record)
                if len(self.history) > HISTORY_MAX:
                    del self.history[: len(self.history) - HISTORY_MAX]
                if self.recorder:
                    self.recorder.write_caption(record)
        self.broadcast({
            "type": "caption", "stream": c.stream, "utt": f"{self.session}-{c.utt_id}",
            "start": c.start, "end": c.end, "committed": c.committed, "tentative": c.tentative,
            "final": c.final, "src": src, "translation": c.translation, "who": who,
        })

    def _on_level(self, stream: str, db: float) -> None:
        self.levels[stream] = max(db, self.levels.get(stream, -100.0))

    # ---- control (worker thread) ----------------------------------------------------
    def _handle(self, msg: dict) -> None:
        try:
            if msg.get("type") == "control":
                self.start() if msg.get("action") == "start" else self.stop()
            elif msg.get("type") == "clear":
                self.clear()
            elif msg.get("type") == "set":
                self.set(str(msg.get("key")), msg.get("value"))
        except Exception as e:  # noqa: BLE001 - report instead of dying
            self.error = f"{type(e).__name__}: {e}"
        self.broadcast(self._status())

    def request_start(self) -> None:
        self._ctl.submit(self._handle, {"type": "control", "action": "start"})

    def request_stop(self) -> None:
        self._ctl.submit(self._handle, {"type": "control", "action": "stop"})

    def request_set(self, key: str, value) -> None:
        self._ctl.submit(self._handle, {"type": "set", "key": key, "value": value})

    def preload(self) -> None:
        """Load the models for the current settings in the background (start is then instant)."""
        self._ctl.submit(self._load_models)

    def _load_models(self) -> None:
        s = self.settings
        try:
            label = engine_label(s.engine)
            loaded = set(self.cache._stt)  # noqa: SLF001 - read-only peek: log only engines loaded now
            first_npu = s.engine == "npu" and not any(models.OV_CACHE_DIR.glob("*.blob"))
            self._set_loading(tr("loading.npu_first") if first_npu else tr("loading.engine", label=label))
            try:
                self.cache.stt(s.engine)
            except Exception as e:  # noqa: BLE001 - e.g. no NPU / NPU driver missing
                if s.engine == "cpu":
                    raise
                print(f"{label} unavailable, falling back to CPU: {e}", flush=True)
                s.engine = "cpu"
                s.save(self.settings_path)
                self._notify(tr("engine.fallback", label=label), secs=15)
                self._set_loading(tr("loading.engine", label="CPU"))
                self.cache.stt("cpu")
            if s.engine not in loaded:
                print(f"speech engine {s.engine}: {self.cache.stt(s.engine).device}", flush=True)
            if s.zh:
                self._set_loading(tr("loading.zh"))
                self.cache.sensevoice()
            self.cache.vad_path()
            self.error = None
        except Exception as e:  # noqa: BLE001
            self.error = tr("error.load", e=e)
        finally:
            self._set_loading(None)

    def _set_loading(self, text: str | None) -> None:
        self.loading = text
        self.broadcast(self._status())

    def start(self) -> None:
        if self.pipeline and self.pipeline.running:
            return
        self._load_models()
        if self.error:
            return
        self.session += 1
        _cpu_percent()  # first sample, so the first [stats] line has a CPU figure
        self.pipeline = Pipeline(self.settings, self.cache, self._on_caption, self._on_level, self._on_audio)
        opened = False
        try:
            self._set_loading(tr("loading.audio"))
            if self.record_on:
                opened = self._open_recorder()
            self.pipeline.start()
            st = self.pipeline.stats()  # models are cached across restarts: count from here
            self._prev_calls = (st["asr_calls"], st["zh_calls"])
            self.error = None
            self._record_event("start", engine=self.settings.engine, sources=list(self.settings.sources))
        except Exception as e:  # noqa: BLE001 - e.g. device unplugged
            self.pipeline.stop()
            self.pipeline = None
            if opened:
                self._close_recorder()
            self.error = tr("error.start", e=e)
        finally:
            self._set_loading(None)

    def stop(self) -> None:
        if self.pipeline:
            self._set_loading(tr("loading.stopping"))
            self.pipeline.stop()  # flushes the last translations first, so they get recorded
            if self.settings.file:  # a replay: every run is a recording of its own
                self._close_recorder()
            else:  # paused: the recording goes on (silence) until ● Record is switched off
                self._record_event("pause")
            self._set_loading(None)

    def clear(self) -> None:
        """Forget the captions so far (the main window and the caption bar clear theirs too);
        a recording already made keeps them."""
        with self._hist_lock:
            self.history.clear()
        self.broadcast({"type": "clear"})

    def _open_recorder(self) -> bool:
        """Start a recording (if none runs) with the captions so far; True if it started one."""
        if self.recorder is not None:
            return False
        s = self.settings
        meta = {"edition": EDITION.name, "settings": {k: v for k, v in dataclasses.asdict(s).items()
                                                      if k not in ("file", "file_me", "speed", "ui_lang")}}
        if s.file:
            meta["replay"] = {"file": s.file, "file_me": s.file_me, "speed": s.speed}
        with self._hist_lock:
            # live: both files follow the clock, whichever sides are on (or switched, or paused) meanwhile
            r = Recorder(self.record_dir, list(STREAMS), align=not s.file,
                         meta={**meta, "captions_before": len(self.history)})
            for record in self.history:
                r.write_caption(record)
            self.recorder = r
        self.last_recording = str(r.dir)
        return True

    def _close_recorder(self) -> None:
        with self._hist_lock:
            r, self.recorder = self.recorder, None
        if r:
            r.close()

    def _record_event(self, name: str, **data) -> None:
        r = self.recorder
        if r:
            r.event(name, **data)

    def set(self, key: str, value) -> None:
        s = self.settings
        if key == "record":  # runtime toggle, not a saved setting
            self.record_on = bool(value)
            if self.record_on and self.pipeline and self.pipeline.running:
                if self._open_recorder():
                    self._record_event("start", engine=s.engine, sources=list(s.sources))
            elif not self.record_on:
                self._close_recorder()
            return
        if key == "api_key":  # {"provider": "deepseek" | "anthropic" | "azure", "key": "..."}; "" forgets it
            keys.save(str(value.get("provider")), value.get("key"))
            self._key_status = keys.status()
            if self.pipeline and self.pipeline.running:  # new key takes effect right away
                self.pipeline.set_model(s.model, s.scene)
            return
        if key == "sources":
            return self._set_sources(value)
        if key == "record_dir":
            return self._set_record_dir(value)
        if key not in {f.name for f in dataclasses.fields(Settings)}:
            return  # UI-only keys (overlay look etc.)
        setattr(s, key, value)
        s.constrained()  # a packaged edition may pin the engine / translation model
        s.save(self.settings_path)
        running = bool(self.pipeline and self.pipeline.running)
        if key == "ui_lang":
            i18n.set_lang(s.ui_lang)
        elif key == "target":
            if running:
                self.pipeline.set_target(value)
            self._record_event("target", value=value)
        elif key in ("model", "scene", "azure_region"):
            if running:
                self.pipeline.set_model(s.model, s.scene)
            if key == "model":
                self._record_event("model", value=value)
        elif key in Settings.RESTART_KEYS:
            if running:
                self.stop()
                self.start()
            elif key in ("engine", "zh"):
                self._load_models()

    def _set_sources(self, value) -> None:
        """Switch a side (system audio / microphone) on or off: at once, without a restart."""
        s, p = self.settings, self.pipeline
        s.sources = [k for k in STREAMS if k in (value or [])]
        s.save(self.settings_path)
        if not (p and p.running) or s.file:
            return
        try:
            p.set_sources(s.sources)
        except Exception:  # e.g. no microphone: keep the setting true to what is running
            s.sources = [k for k in STREAMS if k in p.procs]
            s.save(self.settings_path)
            raise
        finally:
            self._record_event("sources", value=list(s.sources))

    def _set_record_dir(self, value) -> None:
        """Where the next recording goes ("" = the default, Documents\\Mitschrift Live). The
        folder must be creatable and writable - checked now, not when the recording starts. A
        recording that is running stays where it is."""
        path = Path(str(value)).expanduser() if value else DEFAULT_RECORD_DIR
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".mitschrift-write-test"
            probe.write_bytes(b"")
            probe.unlink()
        except OSError as e:
            raise OSError(tr("error.record_dir", path=path, e=e.strerror or e)) from None
        self.settings.record_dir = str(path) if value else ""
        self.settings.save(self.settings_path)
        self.record_dir = path

    def _check_replay_done(self) -> None:
        p = self.pipeline
        if p and p.running and self.settings.file and p.files_done:
            self._ctl.submit(self.stop)

    def _send_devices(self) -> None:
        try:
            system = AudioSystem()
            try:
                msg = {"type": "devices",
                       "speakers": [d["name"] for d in system.loopback_devices()],
                       "mics": [d["name"] for d in system.input_devices()],
                       # apps that use sound, for capturing just one (None: this Windows can't)
                       "apps": self._apps if appaudio.supported() else None}
            finally:
                system.close()
            self.broadcast(msg)
        except Exception:  # noqa: BLE001 - dropdowns just stay at "default"
            pass

    # ---- status -------------------------------------------------------------------
    def _status(self) -> dict:
        s = self.settings
        p = self.pipeline
        running = bool(p and p.running)
        st = p.stats() if p else {}
        engine = self.cache._stt.get(s.engine)  # noqa: SLF001 - read-only peek, no loading here
        return {
            "type": "status", "connected": True, "running": running, "loading": self.loading,
            "version": __version__,
            "edition": {"name": EDITION.name, "lock_engine": EDITION.locked_engine,
                        "engines": list(EDITION.engines) if EDITION.engines else None,
                        "models": list(EDITION.models) if EDITION.models else None},
            "data_dir": str(models.APP_DIR),
            "engine": engine_label(s.engine), "device": engine.device if engine else "",
            "model": s.model if s.target != "off" else tr("translation.off"), "target": s.target, "scene": s.scene,
            "ui_lang": i18n.lang(),
            "settings": dataclasses.asdict(s),
            "sources": p.describe_sources() if running else {},
            "asr_ms": st.get("asr_ms"), "mt_ms": st.get("mt_ms"),
            "error": self.error or (st.get("mt_error") if s.target != "off" else None),
            "lag": round(st.get("lag", 0.0), 1),
            "record_on": self.record_on, "recording": self.recorder is not None,
            "record_secs": round(time.time() - self.recorder.started) if self.recorder else 0,
            "last_recording": self.last_recording,
            "record_dir": str(self.record_dir), "record_dir_default": not s.record_dir,
            "notice": self.notice if time.time() < self._notice_until else None,
            "keys": self._key_status,
        }

    def shutdown(self) -> None:
        try:
            if self.pipeline and self.pipeline.running:
                self.pipeline.stop()
        finally:
            self._close_recorder()
            self._stopping.set()
            self._ctl.shutdown(wait=False, cancel_futures=True)
            time.sleep(0.3)
