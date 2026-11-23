"""Where the main window may be restored: guards for the position and size saved in ui.json.

Windows parks a minimized window at (-32000, -32000); pywebview still reports that move (in
its own units: -16000 at 200 % scaling), so a window that was minimized when the app quit
came back off every screen. Positions and sizes from a minimized window are not saved, and a
saved position that is on no monitor any more (that, or a screen that was unplugged) is
dropped at start so Windows places the window itself.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

PARKED = -10000  # any x/y below this is a minimized window, not a place on a screen
MIN_SIZE = (560, 420)  # the main window's min_size: anything smaller is a minimized one

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
_user32.MonitorFromPoint.restype = wintypes.HMONITOR
MONITOR_DEFAULTTONULL = 0


def keep_position(x: int, y: int) -> bool:
    """Is (x, y) from a 'moved' event a real position (not a minimized window)?"""
    return x > PARKED and y > PARKED


def keep_size(w: int, h: int) -> bool:
    """Is (w, h) from a 'resized' event a real size (not a minimized window)?"""
    return w >= MIN_SIZE[0] and h >= MIN_SIZE[1]


def on_screen(x: int, y: int, w: int = 800) -> bool:
    """Is the title bar of a window at (x, y), w wide, on some monitor? The saved numbers are in
    pywebview's units; whether Windows reads a point in those or in physical pixels depends on
    the calling thread's DPI awareness, so both readings are tried."""
    if not keep_position(x, y):
        return False
    try:
        scale = _user32.GetDpiForSystem() / 96
    except (AttributeError, OSError):
        scale = 1.0
    cx, cy = x + min(w, 400) / 2, y + 15
    for k in {1.0, scale}:
        if _user32.MonitorFromPoint(wintypes.POINT(round(cx * k), round(cy * k)), MONITOR_DEFAULTTONULL):
            return True
    return False


def restorable(saved: dict) -> dict:
    """The saved main-window geometry minus what can't be used: no position if it is on no
    screen, no size if it is a minimized window's."""
    out = dict(saved)
    x, y = out.get("x"), out.get("y")
    if x is not None and y is not None and not on_screen(x, y, out.get("w", 800)):
        out.pop("x", None)
        out.pop("y", None)
    w, h = out.get("w"), out.get("h")
    if w is not None and h is not None and not keep_size(w, h):
        out.pop("w", None)
        out.pop("h", None)
    return out
