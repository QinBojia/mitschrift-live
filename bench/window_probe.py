"""Inspect the desktop UI windows from outside: styles, geometry, a screenshot of the overlay,
and optionally press the global hotkey (Ctrl+Alt+L) to test click-through.

Usage: python window_probe.py [shot.png] [--hotkey]
"""

import ctypes
import sys
import time
from ctypes import wintypes

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixels, same as the screenshot
user32 = ctypes.windll.user32
user32.GetWindowLongW.restype = ctypes.c_long
GWL_STYLE, GWL_EXSTYLE = -16, -20
KEY = "L"  # Ctrl+Alt+<KEY>, the app's click-through hotkey
FLAGS = {0x20: "TRANSPARENT(click-through)", 0x80: "TOOLWINDOW", 0x8: "TOPMOST", 0x80000: "LAYERED",
         0x8000000: "NOACTIVATE", 0x40000: "APPWINDOW"}


def find(title: str) -> int:
    return user32.FindWindowW(None, title)


def describe(title: str) -> tuple[int, wintypes.RECT] | None:
    hwnd = find(title)
    if not hwnd:
        print(f"{title!r}: not found")
        return None
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
    caption = bool(style & 0x00C00000 == 0x00C00000)
    flags = [n for f, n in FLAGS.items() if ex & f]
    print(f"{title!r}: {r.right - r.left}x{r.bottom - r.top} at ({r.left},{r.top}) visible={bool(user32.IsWindowVisible(hwnd))} "
          f"title-bar={caption} ex={flags}")
    return hwnd, r


def press_hotkey() -> None:
    VK_CONTROL, VK_MENU, KEYUP = 0x11, 0x12, 0x2
    for vk in (VK_CONTROL, VK_MENU, ord(KEY)):
        user32.keybd_event(vk, 0, 0, 0)
    for vk in (ord(KEY), VK_MENU, VK_CONTROL):
        user32.keybd_event(vk, 0, KEYUP, 0)


def main() -> None:
    describe("随堂字幕 Mitschrift Live")
    ov = describe("随堂字幕 悬浮字幕")
    if "--hotkey" in sys.argv:
        press_hotkey()
        time.sleep(0.8)
        print(f"after Ctrl+Alt+{KEY}:")
        describe("随堂字幕 悬浮字幕")
    shot = next((a for a in sys.argv[1:] if a.endswith(".png")), None)
    if shot and ov:
        from PIL import ImageGrab

        _, r = ov
        pad = 40
        ImageGrab.grab(bbox=(r.left - pad, r.top - pad, r.right + pad, r.bottom + pad), all_screens=True).save(shot)
        print("saved", shot)


if __name__ == "__main__":
    main()
