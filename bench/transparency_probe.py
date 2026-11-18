"""Which technique gives a truly see-through pywebview (WebView2) window on this machine?

Usage: python transparency_probe.py <mode> <out.png>
  modes: plain      pywebview transparent=True only
         dwm        + DwmExtendFrameIntoClientArea(-1) and a black form background ("glass")
         colorkey   + layered window with a color key matching the form background
         alpha      opaque panel window, rounded corners, layered alpha 0.88
Shows a test window at the top-left over a striped background window for ~3 s, screenshots it.
"""

import ctypes
import sys
import threading
import time
from ctypes import wintypes

import webview

ctypes.windll.shcore.SetProcessDpiAwareness(2)
user32, dwmapi = ctypes.windll.user32, ctypes.windll.dwmapi
user32.GetWindowLongW.restype = ctypes.c_long
GWL_EXSTYLE, WS_EX_LAYERED = -20, 0x80000
LWA_COLORKEY, LWA_ALPHA = 0x1, 0x2

PANEL = """<html><body style="margin:0;background:{body};font-family:Segoe UI">
<div style="margin:{margin};border-radius:16px;background:rgba(12,14,18,.85);color:#fff;padding:14px 22px;font-size:22px">
<div style="font-size:14px;opacity:.7">Guten Morgen zusammen, danke, dass ihr euch heute Zeit genommen habt.</div>
<b>大家早上好，感谢你们今天抽出时间。</b></div></body></html>"""
STRIPES = """<html><body style="margin:0;background:repeating-linear-gradient(45deg,#c33 0 30px,#3a3 30px 60px)"></body></html>"""


class MARGINS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


def main() -> None:
    mode, out = sys.argv[1], sys.argv[2]
    webview.create_window("probe-bg", html=STRIPES, x=20, y=20, width=900, height=260, on_top=True)
    margin = "0" if mode == "alpha" else "30px 30px"
    body = "transparent"
    w = webview.create_window("probe", html=PANEL.format(body=body, margin=margin), x=60, y=40, width=820,
                              height=200 if mode != "alpha" else 120, frameless=True, on_top=True,
                              transparent=mode != "alpha", shadow=False, focus=False)

    def run() -> None:
        time.sleep(2.0)
        hwnd = int(w.native.Handle.ToInt64())
        from System.Drawing import Color  # pythonnet, loaded by pywebview

        if mode == "dwm":
            w.native.BackColor = Color.Black
            dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(-1, -1, -1, -1)))
        elif mode == "colorkey":
            w.native.BackColor = Color.FromArgb(255, 1, 2, 3)
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
            user32.SetLayeredWindowAttributes(hwnd, 0x030201, 0, LWA_COLORKEY)
        elif mode == "alpha":
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), 4)
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
            user32.SetLayeredWindowAttributes(hwnd, 0, int(255 * 0.88), LWA_ALPHA)
        time.sleep(1.2)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        from PIL import ImageGrab

        ImageGrab.grab(bbox=(r.left - 30, r.top - 30, r.right + 30, r.bottom + 30), all_screens=True).save(out)
        print(mode, "saved", out, f"{r.right - r.left}x{r.bottom - r.top}")
        for win in list(webview.windows):
            win.destroy()

    webview.start(lambda: threading.Thread(target=run, daemon=True).start())


if __name__ == "__main__":
    main()
