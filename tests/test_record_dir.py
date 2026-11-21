"""The recordings folder is a setting: checked when it is set, used by the next recording,
kept across launches, and "" goes back to the default."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture import i18n, server  # noqa: E402
from livecapture import pipeline as pl  # noqa: E402
from livecapture.server import Backend  # noqa: E402


def backend(tmp_path, monkeypatch, **settings) -> Backend:
    monkeypatch.setattr(server, "DEFAULT_RECORD_DIR", tmp_path / "Documents" / "Mitschrift Live")
    cfg = tmp_path / "settings.json"  # ui_lang: a backend sets the interface language for the whole process
    pl.Settings(engine="cpu", target="off", zh=False, ui_lang=i18n.lang(), **settings).save(cfg)
    return Backend(cfg)


def test_a_chosen_folder_is_used_by_the_next_recording_and_kept(tmp_path, monkeypatch):
    b = backend(tmp_path, monkeypatch)
    assert b.record_dir == tmp_path / "Documents" / "Mitschrift Live" and b._status()["record_dir_default"]  # noqa: SLF001
    chosen = tmp_path / "D" / "Unterricht"  # does not exist yet: created
    b.set("record_dir", str(chosen))
    assert chosen.is_dir() and b.record_dir == chosen and not b._status()["record_dir_default"]  # noqa: SLF001
    b.record_on = True
    b._open_recorder()  # noqa: SLF001
    assert b.recorder.dir.parent == chosen
    b.shutdown()
    again = Backend(tmp_path / "settings.json")  # the next launch
    assert again.record_dir == chosen
    again.set("record_dir", "")  # back to the default
    assert again.record_dir == tmp_path / "Documents" / "Mitschrift Live" and again.settings.record_dir == ""
    again.shutdown()


def test_a_folder_that_cannot_be_used_changes_nothing(tmp_path, monkeypatch):
    b = backend(tmp_path, monkeypatch)
    before = b.record_dir
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    with pytest.raises(OSError):
        b.set("record_dir", str(blocker / "sub"))  # a folder inside a file
    assert b.record_dir == before and b.settings.record_dir == ""
    b.shutdown()
