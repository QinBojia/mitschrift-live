"""The main window's saved geometry: nothing from a minimized window is kept, and a position on
no screen is dropped at start (livecapture.screen)."""

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture import screen  # noqa: E402


def test_a_minimized_window_is_not_saved():
    assert not screen.keep_position(-16000, -16000)  # -32000 at 200 % scaling
    assert not screen.keep_position(-32000, -32000)
    assert screen.keep_position(-1920, 40)  # a monitor left of the main one
    assert not screen.keep_size(160, 28) and screen.keep_size(1060, 820)


def test_a_position_on_no_screen_is_dropped_at_start():
    broken = {"x": -16000, "y": -16000, "w": 1060, "h": 820}
    assert screen.restorable(broken) == {"w": 1060, "h": 820}
    assert screen.restorable({"x": 100, "y": 100, "w": 160, "h": 28}).keys() <= {"x", "y"}  # tiny: no size
    if ctypes.windll.user32.GetSystemMetrics(80):  # SM_CMONITORS: there is a screen to be on
        assert screen.restorable({"x": 100, "y": 100, "w": 1060, "h": 820}) == {"x": 100, "y": 100, "w": 1060, "h": 820}
    assert screen.restorable({}) == {}
