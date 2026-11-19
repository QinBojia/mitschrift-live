"""Switching sides (system audio / microphone) on and off while the pipeline runs, with fake
devices; and the recorder keeping both files in step with the clock meanwhile."""

import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture import SAMPLE_RATE  # noqa: E402
from livecapture import pipeline as pl  # noqa: E402
from livecapture import recorder as rec  # noqa: E402

BLOCK = np.zeros(320, dtype=np.float32)


class FakeSource:
    log: list[tuple[str, str]] = []

    def __init__(self, system, kind, device=None):
        self.kind, self.name = kind, f"{kind} device"
        FakeSource.log.append(("open", kind))

    def start(self, feed):
        self.feed = feed

    def stop(self):
        FakeSource.log.append(("close", self.kind))


class FakeProc:
    def __init__(self, stream, engine, vad, cfg, on_caption, **kw):
        self.stream, self.cfg, self.blocks, self.stopped, self.lag = stream, cfg, 0, False, 0.0

    def start(self):
        pass

    def feed(self, block):
        self.blocks += 1

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        pass


class FakeAec:
    def __init__(self):
        self.reference = self.capture = 0

    def push_reference(self, block):
        self.reference += 1

    def process_capture(self, block):
        self.capture += 1
        return block


class FakeCache:
    def stt(self, kind):
        return type("Engine", (), {"max_seconds": 30.0, "stats": lambda self: None, "calls": 0})()

    def vad_path(self):
        return None


def make(monkeypatch, sources, aec=True):
    FakeSource.log = []
    monkeypatch.setattr(pl, "LiveSource", FakeSource)
    monkeypatch.setattr(pl, "StreamProcessor", FakeProc)
    monkeypatch.setattr(pl, "EchoCanceller", FakeAec)
    monkeypatch.setattr(pl, "SileroVad", lambda path: None)
    monkeypatch.setattr(pl, "AudioSystem", lambda: type("System", (), {"close": lambda self: None})())
    recorded = []
    settings = pl.Settings(engine="cpu", target="off", zh=False, aec=aec, sources=list(sources))
    p = pl.Pipeline(settings, FakeCache(), lambda c: None, on_audio=lambda k, b: recorded.append(k))
    p.start()
    return p, recorded


def state(p):
    return sorted(p.procs), sorted(p.sources), p._aec is not None  # noqa: SLF001


def test_both_sides_then_microphone_off_and_on(monkeypatch):
    p, recorded = make(monkeypatch, ["other", "me"])
    assert state(p) == (["me", "other"], ["me", "other"], True)
    assert p.procs["me"].cfg.min_silence == 0.8 and p.procs["other"].cfg.min_silence == 0.5
    other = p.procs["other"]

    p.set_sources(["other"])
    assert state(p) == (["other"], ["other"], False)  # the microphone device is closed
    assert p.procs["other"] is other and not other.stopped  # the other side never noticed
    assert ("close", "mic") in FakeSource.log and ("close", "loopback") not in FakeSource.log

    p.set_sources(["other", "me"])
    assert state(p) == (["me", "other"], ["me", "other"], True)
    assert p.procs["other"] is other
    p.stop()


def test_system_audio_off_keeps_the_loopback_as_echo_reference(monkeypatch):
    p, recorded = make(monkeypatch, ["other", "me"])
    other = p.procs["other"]
    p.set_sources(["me"])
    assert state(p) == (["me"], ["me", "other"], True)  # loopback still open: the canceller needs it
    assert other.stopped
    p.sources["other"].feed(BLOCK)  # what the speakers play ...
    p.sources["me"].feed(BLOCK)
    assert p._aec.reference == 1 and p._aec.capture == 1  # noqa: SLF001 - ... still reaches the canceller
    assert other.blocks == 0 and p.procs["me"].blocks == 1  # but is not recognized
    assert recorded == ["me"]  # nor recorded
    p.stop()


def test_without_echo_cancelling_the_loopback_is_closed_too(monkeypatch):
    p, _ = make(monkeypatch, ["other", "me"], aec=False)
    assert state(p) == (["me", "other"], ["me", "other"], False)
    p.set_sources(["me"])
    assert state(p) == (["me"], ["me"], False)
    p.set_sources([])
    assert state(p) == ([], [], False)
    p.set_sources(["other"])
    assert state(p) == (["other"], ["other"], False)
    p.stop()


def test_start_with_one_side_only(monkeypatch):
    p, _ = make(monkeypatch, ["me"])
    assert state(p) == (["me"], ["me", "other"], True)
    p.stop()
    p, _ = make(monkeypatch, ["other"])
    assert state(p) == (["other"], ["other"], False)
    assert FakeSource.log == [("open", "loopback")]
    p.stop()


def test_settings_keep_only_known_sides(monkeypatch):
    assert pl.Settings(sources=["me", "x", "other"]).constrained().sources == ["other", "me"]
    assert pl.Settings(sources=[]).constrained().sources == []
    assert "sources" not in pl.Settings.RESTART_KEYS  # switching must not restart the capture


def test_recording_follows_the_clock_when_a_side_is_off(tmp_path, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(rec.time, "monotonic", lambda: now[0])
    r = rec.Recorder(tmp_path, ["other", "me"], align=True)
    block = np.full(SAMPLE_RATE // 10, 0.1, dtype=np.float32)  # 0.1 s

    def feed(stream, seconds):
        for _ in range(round(seconds * 10)):
            now[0] += 0.1
            r.on_audio(stream, block)

    feed("other", 3.0)            # 0-3 s: system audio only
    now[0] -= 1.0
    feed("me", 1.0)               # the microphone joins at 2 s
    now[0] += 4.0                 # both silent / off until 7 s
    feed("other", 1.0)            # 7-8 s
    r.close()
    other, _ = sf.read(tmp_path / r.dir.name / "other.mp3", dtype="float32")
    me, _ = sf.read(tmp_path / r.dir.name / "me.mp3", dtype="float32")
    assert abs(len(other) / SAMPLE_RATE - 8.0) < 0.15  # 3 s + 4 s of filled silence + 1 s
    assert abs(len(me) / SAMPLE_RATE - 8.0) < 0.15     # 2 s of silence, 1 s, and silence to the same end
    loud = lambda x, a, b: float(np.abs(x[int(a * SAMPLE_RATE): int(b * SAMPLE_RATE)]).mean())  # noqa: E731
    assert loud(other, 0.5, 2.5) > 0.05 and loud(other, 3.5, 6.5) < 0.01 and loud(other, 7.3, 7.9) > 0.05
    assert loud(me, 0.2, 1.7) < 0.01 and loud(me, 2.3, 2.9) > 0.05 and loud(me, 3.5, 7.8) < 0.01


def test_microphone_alone_is_labelled_as_the_room(tmp_path):
    from livecapture import i18n
    from livecapture.segmenter import Caption
    from livecapture.server import Backend

    cfg = tmp_path / "settings.json"  # ui_lang: a backend sets the interface language for the whole process
    pl.Settings(engine="cpu", target="off", sources=["me"], ui_lang=i18n.lang()).save(cfg)
    b = Backend(cfg)
    seen = []
    b.listeners.append(seen.append)
    caption = Caption(stream="me", utt_id=1, start=0.0, end=1.0, committed="Guten Morgen.", tentative="", final=True)
    b._on_caption(caption)  # noqa: SLF001
    b.settings.sources = ["other", "me"]
    b._on_caption(caption)  # noqa: SLF001
    assert [m["who"] for m in seen] == ["room", None]
    b.shutdown()


def test_recording_without_align_just_appends(tmp_path, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(rec.time, "monotonic", lambda: now[0])
    r = rec.Recorder(tmp_path, ["other"])
    now[0] = 50.0  # a replay or the command line: wall time means nothing
    r.on_audio("other", np.zeros(SAMPLE_RATE, dtype=np.float32))
    r.close()
    assert sf.info(tmp_path / r.dir.name / "other.mp3").frames == SAMPLE_RATE
