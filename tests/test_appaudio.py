"""Capturing one app (livecapture.appaudio): which process belongs to which app, which
processes are captured for it, and how the parts are mixed. Process trees are made up after
ones seen on a real PC (New Teams, a Zoom started from Chrome)."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture import appaudio  # noqa: E402

# pid: (exe, parent pid)
PROCS = {
    1: ("explorer.exe", 0),
    10: ("chrome.exe", 1), 11: ("chrome.exe", 10), 12: ("chrome.exe", 10),  # 12: audio service
    20: ("zoom.exe", 10), 21: ("zoom.exe", 20),  # Zoom opened from a link in Chrome; 21: meeting
    30: ("ms-teams.exe", 0),  # its launcher is gone
    31: ("msedgewebview2.exe", 30), 32: ("msedgewebview2.exe", 31),  # 32 plays the call
    40: ("game.exe", 41), 41: ("steam.exe", 1),
}


def test_which_app_a_process_belongs_to():
    root = lambda pid: PROCS[appaudio.app_root(pid, PROCS)][0]  # noqa: E731
    assert root(12) == "chrome.exe"
    assert root(21) == "zoom.exe"  # not Chrome, though Chrome started it
    assert root(32) == "ms-teams.exe"  # through the embedded browser to its host
    assert root(40) == "game.exe"  # not the launcher
    assert appaudio.app_root(999, PROCS) is None


def test_what_is_captured_for_an_app():
    # Chrome's audio service is a direct child of the main process: the main process covers it.
    # (The main Zoom, also a child of it here, comes along: a capture can't leave out a child.)
    assert appaudio.targets("chrome.exe", PROCS) == {10}
    assert appaudio.targets("Zoom.exe", PROCS) == {20}  # the meeting process comes with the main one
    # Teams plays two levels down: the WebView2 browser process is captured too (a capture only
    # takes a process's direct children)
    assert appaudio.targets("ms-teams.exe", PROCS) == {30, 31}
    assert appaudio.targets("game.exe", PROCS) == {40}
    assert appaudio.targets("notrunning.exe", PROCS) == set()


def test_speaker_setting():
    assert appaudio.app_exe("app:Zoom.exe") == "Zoom.exe"
    assert appaudio.app_exe("Speakers (Realtek Audio)") is None
    assert appaudio.app_exe("") is None and appaudio.app_exe("app:") is None


def test_parts_are_mixed_in_step():
    m = appaudio.Mixer()
    m.push("a", np.full(320, 0.1, np.float32))
    m.push("b", np.full(160, 0.2, np.float32))
    out = m.pull()
    assert len(out) == 160 and np.allclose(out, 0.3)  # only what both have
    m.push("b", np.full(160, 0.2, np.float32))
    assert np.allclose(m.pull(), 0.3)
    # a part that stops delivering doesn't hold the other up for more than MAX_SKEW
    m.push("a", np.full(appaudio.MAX_SKEW + 1600, 0.1, np.float32))
    out = m.pull()
    assert len(out) == 1600 and np.allclose(out, 0.1)
    m.remove("a")
    m.remove("b")
    assert len(m.pull()) == 0
