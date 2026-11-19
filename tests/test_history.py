"""Captions from before ● Record go into the recording, clearing them, and a recording that
pausing doesn't split (server.Backend + recorder.Recorder, no audio devices)."""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture import i18n  # noqa: E402
from livecapture import pipeline as pl  # noqa: E402
from livecapture.caption_bar import CaptionBar  # noqa: E402
from livecapture.recorder import Recorder  # noqa: E402
from livecapture.segmenter import Caption  # noqa: E402
from livecapture.server import Backend  # noqa: E402


def backend(tmp_path) -> Backend:
    cfg = tmp_path / "settings.json"  # ui_lang: a backend sets the interface language for the whole process
    pl.Settings(engine="cpu", target="off", zh=False, ui_lang=i18n.lang()).save(cfg)
    b = Backend(cfg)
    b.record_dir = tmp_path / "rec"
    return b


def say(b: Backend, text: str, ago: float = 0.0, final: bool = True) -> None:
    t = time.time() - ago
    b._on_caption(Caption(stream="other", utt_id=len(b.history) + 1, start=t - 1.0, end=t,  # noqa: SLF001
                          committed=text, tentative="", final=final))


def lines(r: Recorder) -> list[dict]:
    return [json.loads(x) for x in (r.dir / "captions.jsonl").read_text(encoding="utf-8").splitlines()]


class FakePipeline:
    running = True

    def stop(self):
        self.running = False

    def stats(self):
        return {}

    def describe_sources(self):
        return {}


def test_a_recording_starts_with_the_captions_said_before_it(tmp_path):
    b = backend(tmp_path)
    say(b, "Erster Satz.", ago=30)
    say(b, "Zweiter Satz.", ago=10)
    say(b, "noch nicht fertig", final=False)  # not finished: not kept
    b.record_on = True
    b._open_recorder()  # noqa: SLF001
    say(b, "Dritter Satz.")
    r = b.recorder
    assert json.loads((r.dir / "meta.json").read_text(encoding="utf-8"))["captions_before"] == 2  # while recording
    b.shutdown()
    got = lines(r)
    assert [x["committed"] for x in got] == ["Erster Satz.", "Zweiter Satz.", "Dritter Satz."]
    assert got[0]["offset"] < -30 and got[1]["offset"] < -10 < got[2]["offset"] + 10  # no audio before 0 s
    assert all(x["who"] is None and x["src"] == "de" and "session" in x for x in got)
    meta = json.loads((r.dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["captions_before"] == 2 and meta["duration"] >= 0 and meta["settings"]["engine"] == "cpu"
    assert abs(meta["started"] - r.started) < 0.01 and "file" not in meta["settings"]


def test_clearing_forgets_the_captions_for_later_recordings(tmp_path):
    b = backend(tmp_path)
    seen = []
    b.listeners.append(seen.append)
    say(b, "Alter Satz.")
    b.clear()
    assert b.history == [] and seen[-1] == {"type": "clear"}
    say(b, "Neuer Satz.")
    b.record_on = True
    b._open_recorder()  # noqa: SLF001
    r = b.recorder
    b.shutdown()
    assert [x["committed"] for x in lines(r)] == ["Neuer Satz."]


def test_pausing_does_not_split_the_recording(tmp_path):
    b = backend(tmp_path)
    b.record_on = True
    b._open_recorder()  # noqa: SLF001
    first = b.recorder
    b.pipeline = FakePipeline()
    b.stop()  # pause
    assert b.recorder is first  # still the same recording
    b.set("record", False)  # ● Record off: now it ends
    assert b.recorder is None
    meta = json.loads((first.dir / "meta.json").read_text(encoding="utf-8"))
    assert [e["event"] for e in meta["events"]] == ["pause"] and "ended" in meta
    b.shutdown()


def test_two_recordings_in_one_second_get_folders_of_their_own(tmp_path):
    a, b = Recorder(tmp_path, ["other"]), Recorder(tmp_path, ["other"])
    a.close()
    b.close()
    assert a.dir != b.dir and {a.dir.parent, b.dir.parent} == {tmp_path}


def test_the_caption_bar_clears_too():
    bar = CaptionBar({}, None, lambda *a: None, lambda *a: None)
    bar.apply({"type": "caption", "stream": "other", "utt": "1-1", "final": True, "committed": "Hallo.", "tentative": ""})
    assert len(bar._entries) == 1  # noqa: SLF001
    bar.apply({"type": "clear"})
    assert len(bar._entries) == 0  # noqa: SLF001
