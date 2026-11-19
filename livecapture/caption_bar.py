"""Floating caption bar drawn by us (no WebView): per-pixel alpha via UpdateLayeredWindow.

The bar used to be a WebView2 window, which Windows can only make see-through as a whole
(per-pixel transparency left an opaque box, bench/transparency_probe.py). Drawing it
ourselves - text with Pillow, composited with numpy - lets text and background have their
own opacity, e.g. solid captions on a 30 % background or on none at all.

The window lives on its own thread with its own message loop. It never takes focus
(clicking it leaves Zoom focused), is dragged anywhere on its body, resized at its edges,
and shows its tools (A− A+ 显示内容 穿透 ✕) while the mouse is over it. All public methods
are thread-safe.

The newest caption sits at the bottom and older ones fill the space above it, as many
whole lines as the bar is tall: drag it taller to see more of what was said.
"""

from __future__ import annotations

import ctypes
import functools
import math
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .i18n import lang, tr
from .stt import is_cjk, join_text
from .translate import PENDING

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_POPUP = 0x80000000
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x08000000
GWL_EXSTYLE = -20
WM_DESTROY, WM_MOVE, WM_SIZE, WM_CLOSE = 0x0002, 0x0003, 0x0005, 0x0010
WM_SETCURSOR, WM_MOUSEACTIVATE, WM_GETMINMAXINFO = 0x0020, 0x0021, 0x0024
WM_NCHITTEST, WM_TIMER = 0x0084, 0x0113
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0200, 0x0201, 0x0202
WM_CAPTURECHANGED, WM_EXITSIZEMOVE, WM_DPICHANGED = 0x0215, 0x0232, 0x02E0
WM_APP_REDRAW, WM_APP_STYLE = 0x8001, 0x8002
MA_NOACTIVATE = 3
HTCLIENT, HTCAPTION = 1, 2
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
ULW_ALPHA = 2
SPI_GETWORKAREA = 0x30
MONITOR_DEFAULTTONULL = 0
IDC_ARROW, IDC_SIZENWSE, IDC_SIZENESW, IDC_SIZEWE, IDC_SIZENS = 32512, 32642, 32643, 32644, 32645
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", wintypes.POINT), ("ptMaxSize", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("ptMinTrackSize", wintypes.POINT), ("ptMaxTrackSize", wintypes.POINT)]


def _sig(dll, name, restype, *argtypes):
    f = getattr(dll, name)
    f.restype, f.argtypes = restype, list(argtypes)


H, U, W, L = wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
_sig(user32, "DefWindowProcW", LRESULT, H, U, W, L)
_sig(user32, "RegisterClassExW", wintypes.ATOM, ctypes.POINTER(WNDCLASSEXW))
_sig(user32, "CreateWindowExW", H, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, H, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
_sig(user32, "PostMessageW", wintypes.BOOL, H, U, W, L)
_sig(user32, "GetMessageW", wintypes.BOOL, ctypes.POINTER(wintypes.MSG), H, U, U)
_sig(user32, "TranslateMessage", wintypes.BOOL, ctypes.POINTER(wintypes.MSG))
_sig(user32, "DispatchMessageW", LRESULT, ctypes.POINTER(wintypes.MSG))
_sig(user32, "GetWindowRect", wintypes.BOOL, H, ctypes.POINTER(wintypes.RECT))
_sig(user32, "SetWindowPos", wintypes.BOOL, H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, U)
_sig(user32, "GetWindowLongW", ctypes.c_long, H, ctypes.c_int)
_sig(user32, "SetWindowLongW", ctypes.c_long, H, ctypes.c_int, ctypes.c_long)
_sig(user32, "UpdateLayeredWindow", wintypes.BOOL, H, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
     ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.COLORREF,
     ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD)
_sig(user32, "SetTimer", ctypes.c_size_t, H, ctypes.c_size_t, U, ctypes.c_void_p)
_sig(user32, "KillTimer", wintypes.BOOL, H, ctypes.c_size_t)
_sig(user32, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
_sig(user32, "LoadCursorW", wintypes.HANDLE, wintypes.HINSTANCE, ctypes.c_void_p)
_sig(user32, "SetCursor", wintypes.HANDLE, wintypes.HANDLE)
_sig(user32, "SetCapture", H, H)
_sig(user32, "ReleaseCapture", wintypes.BOOL)
_sig(user32, "GetDpiForWindow", U, H)
_sig(user32, "SetThreadDpiAwarenessContext", ctypes.c_void_p, ctypes.c_void_p)
_sig(user32, "SystemParametersInfoW", wintypes.BOOL, U, U, ctypes.c_void_p, U)
_sig(user32, "MonitorFromRect", wintypes.HMONITOR, ctypes.POINTER(wintypes.RECT), wintypes.DWORD)
_sig(user32, "ShowWindow", wintypes.BOOL, H, ctypes.c_int)
_sig(user32, "ShowWindowAsync", wintypes.BOOL, H, ctypes.c_int)
_sig(user32, "GetDpiForSystem", U)
_sig(user32, "DestroyWindow", wintypes.BOOL, H)
_sig(user32, "PostQuitMessage", None, ctypes.c_int)
_sig(gdi32, "CreateCompatibleDC", wintypes.HDC, wintypes.HDC)
_sig(gdi32, "CreateDIBSection", wintypes.HBITMAP, wintypes.HDC, ctypes.c_void_p, U,
     ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD)
_sig(gdi32, "SelectObject", wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_sig(gdi32, "DeleteObject", wintypes.BOOL, wintypes.HGDIOBJ)
_sig(gdi32, "DeleteDC", wintypes.BOOL, wintypes.HDC)
_sig(kernel32, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR)

# ---------------------------------------------------------------- look (CSS of the old overlay)

FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
PANEL = (0x0C, 0x0E, 0x12)  # background
WHO_COLOR = {"other": (0x5E, 0xE0, 0xD4), "me": (0x9B, 0xE2, 0x7B)}
ACCENT = (0x6E, 0xA8, 0xFF)
WHITE = (255, 255, 255)
HOVER_BG_FLOOR = 0.3  # under the mouse the panel shows at least this much, to see where to drag
TOOLS = [("smaller", "A−"), ("bigger", "A+"), ("tr", ""), ("lock", "bar.lock"), ("close", "×")]  # i18n keys or text ("tr": the display mode)
NO_LINE_START = set("，。、？！：；,.?!:;…）)」』”’")
KEEP = 80  # captions remembered; the bar shows as many of the newest as fit its height
MIN_FRAME_S = 0.12  # caption updates are drawn at most ~8 times a second (a frame costs ~25 ms)
SHOW_MODES = ("both", "original", "translation")  # what a caption shows; the bar button cycles through them
# font: px (at 100 % display scaling) of every caption line. font_v 2: since 0.3.3 every line has
# that size; before, "font" was the newest line's size and earlier lines got 0.78 of it - the size
# the user chose to keep for all (an old setting is converted, see CaptionBar).
DEFAULTS = {"font": 20, "text_alpha": 1.0, "bg_alpha": 1.0, "show": "both", "font_v": 2}
FONT_RANGE = (12, 40)


def entry_lines(e: dict, mode: str) -> tuple[list[tuple[str, float]], tuple[str, float] | None]:
    """What one caption shows in a display mode: the main line as (text, opacity) runs, and
    the smaller translation line as (text, opacity), or None.

    "translation": the translation takes the place of the original. Until it is there the
    original stands in, dimmed, so the bar is never empty while someone speaks; a finished
    caption that gets no translation (translation off, no key, already in the target
    language) keeps its original at full strength.
    """
    committed, tentative = e.get("committed", ""), e.get("tentative", "")
    sep = " " if committed and tentative and join_text(committed, tentative) != committed + tentative else ""
    tr_text = e.get("translation")
    pending = tr_text == PENDING
    if mode == "translation" and tr_text and not pending:
        return [(tr_text, 1.0)], None
    k = 0.6 if mode == "translation" and (pending or not e.get("final")) else 1.0
    main = [(committed, k), (sep + tentative, 0.55 * k)]
    if mode == "both" and tr_text:
        return main, ((tr("bar.pending"), 0.6) if pending else (tr_text, 1.0))
    return main, None


@functools.lru_cache(maxsize=64)
def _font(px: int, bold: bool) -> ImageFont.FreeTypeFont:
    for name, index in ((("msyhbd.ttc" if bold else "msyh.ttc"), 1), (("segoeuib.ttf" if bold else "segoeui.ttf"), 0)):
        try:
            return ImageFont.truetype(str(FONT_DIR / name), px, index=index)  # 1 = "Microsoft YaHei UI"
        except OSError:
            continue
    return ImageFont.load_default(px)


def _units(text: str) -> list[str]:
    """Line-break units: every CJK character alone, other text word by word (with its space)."""
    out, buf = [], ""
    for ch in text:
        if is_cjk(ch) or ch in NO_LINE_START and not buf:
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        elif ch == " ":
            out.append(buf + ch)
            buf = ""
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def _wrap(runs: list[tuple[str, tuple, float]], font, width: float) -> list[list[tuple[float, str, tuple, float]]]:
    """runs of (text, rgb, alpha) -> lines of (x, unit, rgb, alpha)."""
    lines: list[list] = [[]]
    x = 0.0
    for text, rgb, a in runs:
        for u in _units(text):
            w = font.getlength(u)
            if x > 0 and x + font.getlength(u.rstrip()) > width and u not in NO_LINE_START:
                lines.append([])
                x = 0.0
            if x == 0 and w > width:  # a word wider than the bar: split it
                part = ""
                for ch in u:
                    if part and font.getlength(part + ch) > width:
                        lines[-1].append((0.0, part, rgb, a))
                        lines.append([])
                        part = ""
                    part += ch
                u, w = part, font.getlength(part)
            lines[-1].append((x, u, rgb, a))
            x += w
    return [ln for ln in lines if ln] or [[]]


@functools.lru_cache(maxsize=4)
def _round_mask(w: int, h: int, r: int) -> Image.Image:
    k = 3  # supersampled, for smooth corners
    m = Image.new("L", (w * k, h * k))
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w * k - 1, h * k - 1), r * k, fill=255)
    return m.resize((w, h), Image.LANCZOS)


def speaker_color(e: dict) -> tuple:
    """Colour of the bar left of a caption: who spoke (the microphone alone hears the room,
    shown like the other side)."""
    return WHO_COLOR["other"] if e.get("who") == "room" else WHO_COLOR.get(e["stream"], WHITE)


Row = tuple  # (y top, font, line height, [(x, text, rgb, alpha)], (caption index, bar rgb) | None)


def layout(w: int, h: int, scale: float, entries: list[dict], prefs: dict) -> list[Row]:
    """The lines the bar shows, bottom-up: the newest caption at the bottom, earlier ones above
    it for as many whole lines as fit. Every line has the same size and line height, whatever
    its language, original or translation, newest or not: the original is bold, a translation
    regular. Who spoke is a coloured bar left of each caption (render), not a label line."""
    px = lambda v: max(1, round(v * scale))  # noqa: E731
    size = px(prefs["font"])
    bold, regular = _font(size, True), _font(size, False)
    lh, gap = round(size * 1.35), round(size * 0.4)
    pad_l, pad_r, pad_t, pad_b = px(24), px(24), px(8), px(14)
    width = w - pad_l - pad_r
    if not entries:
        return [(h - pad_b - lh, regular, lh, [(0.0, tr("bar.idle"), WHITE, 0.45)], None)]
    rows: list[Row] = []
    y = h - pad_b
    for i in range(len(entries) - 1, -1, -1):
        e = entries[i]
        main, line = entry_lines(e, prefs["show"])
        # "translation only" with the translation there: the main line is the translation
        fm = regular if prefs["show"] == "translation" and e.get("translation") not in (None, "", PENDING) else bold
        lines = [(fm, ln) for ln in _wrap([(s, WHITE, a) for s, a in main], fm, width)]
        if line:
            lines += [(regular, ln) for ln in _wrap([(line[0], WHITE, line[1])], regular, width)]
        if i < len(entries) - 1:
            y -= gap  # between captions
        for font, items in reversed(lines):
            if y - lh < pad_t:
                return rows
            y -= lh
            rows.append((y, font, lh, items, (i, speaker_color(e))))
    return rows


def _draw_rows(draw: ImageDraw.ImageDraw, rows: list[Row], x0: int, text_alpha: float) -> list[tuple[tuple, tuple]]:
    """Draws the rows into an alpha layer (all text is one colour per run, so its coverage is
    all a layer needs); returns (box, rgb) for the runs that are not white."""
    tints = []
    for y, font, lh, items, _ in rows:
        asc, desc = font.getmetrics()
        base = y + (lh - asc - desc) / 2 + asc
        i = 0
        while i < len(items):  # one draw call per run of equal colour
            x, s, rgb, a = items[i]
            i += 1
            while i < len(items) and items[i][2:] == (rgb, a):
                s += items[i][1]
                i += 1
            if s.strip():
                draw.text((x0 + x, base), s, font=font, fill=round(255 * a * text_alpha), anchor="ls")
                if rgb != WHITE:
                    tints.append(((math.floor(x0 + x), y, math.ceil(x0 + x + font.getlength(s)) + 1, y + lh), rgb))
    return tints


def _mix(a: tuple, b: tuple, k: float) -> tuple:
    """Colour a, k of the way to colour b."""
    return tuple(round(p + (q - p) * k) for p, q in zip(a, b, strict=True))


def render(w: int, h: int, scale: float, entries: list[dict], prefs: dict, *, hover: bool,
           hot: str | None, locked: bool) -> tuple[bytes, dict[str, tuple[int, int, int, int]]]:
    """The bar as premultiplied BGRA bytes, plus the tool buttons' rectangles (when shown).

    Layers are straight RGBA composited by Pillow (in C: a frame full of text costs a few ms;
    the float maths of earlier versions took ~60 ms for the whole bar): panel, text shadow,
    text, tools."""
    px = lambda v: max(1, round(v * scale))  # noqa: E731
    alpha = Image.new("L", (w, h))
    draw = ImageDraw.Draw(alpha)
    rows = layout(w, h, scale, entries, prefs)
    tints = _draw_rows(draw, rows, px(24), prefs["text_alpha"])
    # who spoke: a bar in the speaker's colour left of the captions, as tall as their visible
    # lines; one bar for a run of captions by the same side (across the gaps between them)
    spans: dict[int, list] = {}
    for y, font, lh, _, bar in rows:
        if bar:
            s = spans.setdefault(bar[0], [y, y + lh, bar[1], font.size])
            s[0], s[1] = min(s[0], y), max(s[1], y + lh)
    runs: list[list] = []  # [top, bottom, rgb, size, last caption index], oldest first
    for i in sorted(spans):
        top, bottom, rgb, size = spans[i]
        if runs and runs[-1][4] == i - 1 and runs[-1][2] == rgb:
            runs[-1][1], runs[-1][4] = bottom, i
        else:
            runs.append([top, bottom, rgb, size, i])
    bw, bx = max(2, px(3)), px(12)
    for top, bottom, rgb, size, _ in runs:
        inset = round(size * 0.18)
        box = (bx, top + inset, bx + bw - 1, bottom - inset)
        draw.rounded_rectangle(box, bw // 2, fill=round(255 * prefs["text_alpha"]))
        tints.append(((bx, top, bx + bw, bottom), rgb))
    bg_a = prefs["bg_alpha"] if not hover or locked else max(prefs["bg_alpha"], HOVER_BG_FLOOR)
    out = _panel(w, h, px(16), round(bg_a * 255), locked).copy()
    if box := alpha.getbbox():
        if bg_a < 0.95:  # on the opaque dark panel a shadow is invisible: skip the work
            blur, dy = px(1.6), px(1)
            k = (0.55 + 0.4 * (1 - bg_a)) * 1.6  # stronger the more see-through the panel
            sb = _grow(box, 3 * blur, w, h)
            shadow = Image.new("RGBA", (sb[2] - sb[0], sb[3] - sb[1]))  # black
            shadow.putalpha(alpha.crop(sb).filter(ImageFilter.GaussianBlur(blur)).point(lambda v: min(255, round(v * k))))
            out.alpha_composite(shadow, (sb[0], sb[1] + dy))
        color = Image.new("RGB", (box[2] - box[0], box[3] - box[1]), WHITE)
        for (x0, y0, x1, y1), rgb in tints:
            color.paste(rgb, (max(0, x0 - box[0]), max(0, y0 - box[1]), min(color.width, x1 - box[0]), min(color.height, y1 - box[1])))
        layer = color.convert("RGBA")
        layer.putalpha(alpha.crop(box))
        out.alpha_composite(layer, box[:2])

    # ---- tools (only under the mouse; a click-through bar can't be clicked anyway), on a
    # backing of their own: the captions fill the bar up to its top edge
    rects: dict[str, tuple[int, int, int, int]] = {}
    if hover and not locked:
        f = _font(px(12), False)
        asc, desc = f.getmetrics()
        bh = asc + desc + px(8)
        x, labels = w - px(8), []
        for name, label in reversed(TOOLS):
            if name == "tr":  # shows the current display mode; a click moves on to the next
                label = tr(f"bar.show.{prefs['show']}")
            else:
                label = tr(label) if label.startswith("bar.") else label
            bw = round(f.getlength(label)) + px(14)
            rects[name] = (x - bw, px(6), x, px(6) + bh)
            labels.append((name, label))
            x -= bw + px(2)
        tb = (max(0, x - px(2)), px(4), w - px(6), px(8) + bh)
        tools = Image.new("RGBA", (tb[2] - tb[0], tb[3] - tb[1]))
        d = ImageDraw.Draw(tools)
        d.rounded_rectangle((0, 0, tools.width - 1, tools.height - 1), px(8), fill=(*PANEL, 235))
        for name, label in labels:
            r = rects[name]
            r = (r[0] - tb[0], r[1] - tb[1], r[2] - tb[0] - 1, r[3] - tb[1] - 1)
            if name == hot:
                d.rounded_rectangle(r, px(6), fill=(*_mix(PANEL, WHITE, 0.14), 235))
            color = ACCENT if name == "tr" else WHITE if name == hot else _mix(PANEL, WHITE, 0.55)
            d.text((r[0] + px(7), r[1] + px(4) + asc), label, font=f, fill=(*color, 255), anchor="ls")
        out.alpha_composite(tools, tb[:2])
    return out.convert("RGBa").tobytes("raw", "BGRa"), rects


def _grow(box, m: int, w: int, h: int):
    return max(0, box[0] - m), max(0, box[1] - m), min(w, box[2] + m), min(h, box[3] + m)


@functools.lru_cache(maxsize=4)
def _panel(w: int, h: int, r: int, alpha: int, locked: bool) -> Image.Image:
    """The panel, straight RGBA. Unless click-through, alpha is never 0: pixels with alpha 0
    let clicks fall through, and the whole bar should stay grabbable."""
    floor = 0 if locked else 1
    im = Image.new("RGBA", (w, h), (*PANEL, 0))
    im.putalpha(_round_mask(w, h, r).point(lambda v: max(floor, round(v * alpha / 255))))
    return im


# ---------------------------------------------------------------- window

class CaptionBar:
    def __init__(self, prefs: dict, geometry: dict | None, on_prefs: Callable[[dict, dict], None],
                 on_command: Callable[[str], None]):
        """prefs: font / text_alpha / bg_alpha / show; geometry: x y w h in physical px.

        on_prefs(prefs, geometry) after any change (to save them); on_command("lock" | "hide").
        """
        if "show" not in prefs and prefs.get("translation") is False:  # saved by 0.3.0 and earlier
            prefs = {**prefs, "show": "original"}
        if "font" in prefs and prefs.get("font_v") != DEFAULTS["font_v"]:  # saved by 0.3.2 and earlier
            prefs = {**prefs, "font": round(prefs["font"] * 0.78), "font_v": DEFAULTS["font_v"]}
        self.prefs = {**DEFAULTS, **{k: v for k, v in prefs.items() if k in DEFAULTS}}
        if self.prefs["show"] not in SHOW_MODES:
            self.prefs["show"] = "both"
        self.prefs["font"] = max(FONT_RANGE[0], min(FONT_RANGE[1], int(self.prefs["font"])))
        self.geometry = geometry
        self._on_prefs, self._on_command = on_prefs, on_command
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, dict] = OrderedDict()
        self._hwnd = None
        self._visible, self._locked = True, False
        self._hover, self._hot = False, None
        self._rects: dict = {}
        self._edge: str | None = None
        self._resize: tuple | None = None
        self._pending = False
        self._last_paint = 0.0
        self._drawn_lang = lang()
        self._ready = threading.Event()

    # ---- public, any thread
    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="caption-bar").start()
        self._ready.wait(5)

    def apply(self, m: dict) -> None:
        """A backend message: captions (same merge rules as ui/client.js); a status message
        only matters when the interface language changed (labels are drawn into the bar)."""
        if m.get("type") == "status":
            if m.get("ui_lang") and m["ui_lang"] != self._drawn_lang:
                self._drawn_lang = m["ui_lang"]
                self._request_redraw()
            return
        if m.get("type") == "clear":  # the user cleared the captions (main window)
            with self._lock:
                self._entries.clear()
            self._request_redraw()
            return
        if m.get("type") != "caption":
            return
        key = f"{m['stream']}:{m['utt']}"
        with self._lock:
            if m.get("final") and not m.get("committed"):
                self._entries.pop(key, None)
            else:
                self._entries[key] = {**self._entries.get(key, {}), **m}
                while len(self._entries) > KEEP:
                    self._entries.popitem(last=False)
        self._request_redraw()

    def set_pref(self, key: str, value) -> None:
        if key == "font":
            value = max(FONT_RANGE[0], min(FONT_RANGE[1], int(value)))
        elif key == "text_alpha":
            value = max(0.2, min(1.0, float(value)))
        elif key == "bg_alpha":
            value = max(0.0, min(1.0, float(value)))
        elif key == "show":
            if value not in SHOW_MODES:
                return
        else:
            return
        with self._lock:
            self.prefs[key] = value
        self._on_prefs(dict(self.prefs), self.geometry)
        self._request_redraw()

    def set_visible(self, on: bool) -> None:
        self._visible = on
        if self._hwnd:  # async: never wait on the bar's thread from another one
            user32.ShowWindowAsync(self._hwnd, SW_SHOWNOACTIVATE if on else SW_HIDE)
            if on:
                self._request_redraw()

    def set_click_through(self, on: bool) -> None:
        self._locked = on
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_APP_STYLE, 0, 0)

    def destroy(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    # ---- window thread
    def _request_redraw(self) -> None:
        if self._hwnd and not self._pending:
            self._pending = True
            user32.PostMessageW(self._hwnd, WM_APP_REDRAW, 0, 0)

    def _run(self) -> None:
        user32.SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        hinst = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wndproc)  # keep a reference: ctypes callbacks must outlive the window
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._proc, hInstance=hinst,
                         hCursor=user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW)),
                         lpszClassName="LiveCaptureCaptionBar")
        user32.RegisterClassExW(ctypes.byref(wc))
        x, y, w, h = self._initial_rect()
        ex = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
        self._hwnd = user32.CreateWindowExW(ex, "LiveCaptureCaptionBar", "随堂字幕 悬浮字幕", WS_POPUP,
                                            x, y, w, h, None, None, hinst, None)
        self._paint()
        if self._visible:
            user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
        user32.SetTimer(self._hwnd, 1, 100, None)  # hover polling (tools + hover background)
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _initial_rect(self) -> tuple[int, int, int, int]:
        g = self.geometry or {}
        if all(k in g for k in "xywh"):
            r = wintypes.RECT(g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"])
            if user32.MonitorFromRect(ctypes.byref(r), MONITOR_DEFAULTTONULL):  # still on a screen
                return g["x"], g["y"], g["w"], g["h"]
        wa = wintypes.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(wa), 0)
        scale = self._system_scale()
        sw = wa.right - wa.left
        w, h = min(round(1100 * scale), int(sw * 0.7)), round(210 * scale)
        return wa.left + (sw - w) // 2, wa.bottom - h - round(60 * scale), w, h

    @staticmethod
    def _system_scale() -> float:
        try:
            return user32.GetDpiForSystem() / 96
        except Exception:  # noqa: BLE001
            return 1.0

    def _scale(self) -> float:
        return (user32.GetDpiForWindow(self._hwnd) or 96) / 96

    def _rect(self) -> wintypes.RECT:
        r = wintypes.RECT()
        user32.GetWindowRect(self._hwnd, ctypes.byref(r))
        return r

    def _paint(self, rect: wintypes.RECT | None = None) -> None:
        self._pending = False
        self._last_paint = time.monotonic()
        r = rect or self._rect()
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            return
        with self._lock:
            entries = [dict(e) for e in self._entries.values()]
            prefs = dict(self.prefs)
        data, self._rects = render(w, h, self._scale(), entries, prefs,
                                   hover=self._hover, hot=self._hot, locked=self._locked)
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                               biPlanes=1, biBitCount=32, biCompression=0)
        bits = ctypes.c_void_p()
        mem = gdi32.CreateCompatibleDC(None)
        bmp = gdi32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not bmp:
            gdi32.DeleteDC(mem)
            return
        ctypes.memmove(bits, data, len(data))
        old = gdi32.SelectObject(mem, bmp)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, per-pixel alpha
        user32.UpdateLayeredWindow(self._hwnd, None, ctypes.byref(wintypes.POINT(r.left, r.top)),
                                   ctypes.byref(wintypes.SIZE(w, h)), mem, ctypes.byref(wintypes.POINT(0, 0)),
                                   0, ctypes.byref(blend), ULW_ALPHA)
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)

    def _save_geometry(self) -> None:
        r = self._rect()
        self.geometry = {"x": r.left, "y": r.top, "w": r.right - r.left, "h": r.bottom - r.top}
        self._on_prefs(dict(self.prefs), self.geometry)

    def _tool_at(self, cx: int, cy: int) -> str | None:
        for name, (x0, y0, x1, y1) in self._rects.items():
            if x0 <= cx < x1 and y0 <= cy < y1:
                return name
        return None

    def _edge_at(self, cx: int, cy: int, w: int, h: int) -> str | None:
        b = round(6 * self._scale())
        e = ("n" if cy < b else "s" if cy >= h - b else "") + ("w" if cx < b else "e" if cx >= w - b else "")
        return e or None

    def _wndproc(self, hwnd, msg, wp, lp):  # noqa: C901 - one switch over window messages
        try:
            if msg == WM_APP_REDRAW:  # caption updates: at most one frame per MIN_FRAME_S
                wait = self._last_paint + MIN_FRAME_S - time.monotonic()
                if wait > 0:
                    user32.SetTimer(hwnd, 2, max(10, round(wait * 1000)), None)
                else:
                    self._paint()
                return 0
            if msg == WM_APP_STYLE:
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                ex = ex | WS_EX_TRANSPARENT if self._locked else ex & ~WS_EX_TRANSPARENT
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
                self._paint()
                return 0
            if msg == WM_TIMER and wp == 2:  # deferred caption frame
                user32.KillTimer(hwnd, 2)
                self._paint()
                return 0
            if msg == WM_TIMER:
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                r = self._rect()
                inside = r.left <= pt.x < r.right and r.top <= pt.y < r.bottom
                hover = inside and not self._locked
                hot = self._tool_at(pt.x - r.left, pt.y - r.top) if hover else None
                if (hover, hot) != (self._hover, self._hot):
                    self._hover, self._hot = hover, hot
                    self._paint()
                return 0
            if msg == WM_MOUSEACTIVATE:
                return MA_NOACTIVATE
            if msg == WM_NCHITTEST:
                x, y = ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value
                r = self._rect()
                cx, cy = x - r.left, y - r.top
                self._edge = self._edge_at(cx, cy, r.right - r.left, r.bottom - r.top)
                if self._edge or self._tool_at(cx, cy):
                    return HTCLIENT
                return HTCAPTION  # drag the bar by its body
            if msg == WM_SETCURSOR and (lp & 0xFFFF) == HTCLIENT and self._edge:
                cur = {"n": IDC_SIZENS, "s": IDC_SIZENS, "w": IDC_SIZEWE, "e": IDC_SIZEWE,
                       "nw": IDC_SIZENWSE, "se": IDC_SIZENWSE, "ne": IDC_SIZENESW, "sw": IDC_SIZENESW}[self._edge]
                user32.SetCursor(user32.LoadCursorW(None, ctypes.c_void_p(cur)))
                return 1
            if msg == WM_LBUTTONDOWN and self._edge:
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                r = self._rect()
                self._resize = (self._edge, (r.left, r.top, r.right, r.bottom), (pt.x, pt.y))
                user32.SetCapture(hwnd)
                return 0
            if msg == WM_MOUSEMOVE and self._resize:
                self._do_resize()
                return 0
            if msg == WM_LBUTTONUP:
                if self._resize:
                    self._resize = None
                    user32.ReleaseCapture()
                    self._save_geometry()
                    return 0
                x, y = ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value
                self._click(self._tool_at(x, y))
                return 0
            if msg == WM_CAPTURECHANGED and self._resize:
                self._resize = None
                self._save_geometry()
                return 0
            if msg == WM_EXITSIZEMOVE:  # end of a drag
                self._save_geometry()
                return 0
            if msg == WM_DPICHANGED:  # moved to a screen with another scale
                r = ctypes.cast(lp, ctypes.POINTER(wintypes.RECT)).contents
                user32.SetWindowPos(hwnd, None, r.left, r.top, r.right - r.left, r.bottom - r.top,
                                    SWP_NOZORDER | SWP_NOACTIVATE)
                self._paint()
                return 0
            if msg == WM_GETMINMAXINFO:
                mmi = ctypes.cast(lp, ctypes.POINTER(MINMAXINFO)).contents
                s = (user32.GetDpiForWindow(hwnd) or 96) / 96 if self._hwnd else 1.0
                mmi.ptMinTrackSize = wintypes.POINT(round(360 * s), round(80 * s))
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception as e:  # noqa: BLE001 - never let an exception cross the C boundary
            print(f"caption bar: {type(e).__name__}: {e}", flush=True)
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    def _do_resize(self) -> None:
        edge, (l0, t0, r0, b0), (px0, py0) = self._resize
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        dx, dy = pt.x - px0, pt.y - py0
        s = self._scale()
        min_w, min_h = round(360 * s), round(80 * s)
        left, top, right, bottom = l0, t0, r0, b0
        if "w" in edge:
            left = min(l0 + dx, r0 - min_w)
        if "e" in edge:
            right = max(r0 + dx, l0 + min_w)
        if "n" in edge:
            top = min(t0 + dy, b0 - min_h)
        if "s" in edge:
            bottom = max(b0 + dy, t0 + min_h)
        self._paint(wintypes.RECT(left, top, right, bottom))

    def _click(self, name: str | None) -> None:
        if name == "smaller":
            self.set_pref("font", self.prefs["font"] - 2)
        elif name == "bigger":
            self.set_pref("font", self.prefs["font"] + 2)
        elif name == "tr":
            self.set_pref("show", SHOW_MODES[(SHOW_MODES.index(self.prefs["show"]) + 1) % len(SHOW_MODES)])
        elif name == "lock":
            self._on_command("lock")
        elif name == "close":
            self._on_command("hide")


# ---------------------------------------------------------------- demo feed (--demo, no backend)

DEMO = [
    ("other", "Guten Morgen zusammen, danke, dass ihr euch heute Zeit genommen habt.", "大家早上好，感谢你们今天抽出时间。"),
    ("me", "Können Sie das bitte noch einmal langsamer erklären?", "您能再慢一点解释一遍吗？"),
    ("other", "Natürlich. Der Nebensatz steht am Ende, weil vorne ein weil steht.", "当然。从句放在最后，因为前面有 weil。"),
    ("other", "大家注意，这是一个中性名词，所以要说 das Ehrenamt。", None),
    ("me", "Thanks, that makes sense now.", "谢谢，现在明白了。"),
]


def demo_feed(apply: Callable[[dict], None], stop: threading.Event) -> None:
    """Scripted captions that grow word by word, then get a translation (for --demo)."""
    n = 0
    while not stop.is_set():
        for stream, text, zh in DEMO:
            n += 1
            words = text.split(" ") if " " in text else list(text)
            joiner = " " if " " in text else ""
            step = max(1, math.ceil(len(words) / 6))
            for i in range(step, len(words) + step, step):
                if stop.wait(0.35):
                    return
                shown = words[:i]
                cut = max(0, len(shown) - 2)
                apply({"type": "caption", "stream": stream, "utt": f"demo-{n}", "final": False,
                       "committed": joiner.join(shown[:cut]), "tentative": joiner.join(shown[cut:]),
                       "translation": None})
            final = {"type": "caption", "stream": stream, "utt": f"demo-{n}", "final": True,
                     "committed": text, "tentative": ""}
            apply({**final, "translation": PENDING if zh else None})
            if zh:
                time.sleep(0.8)
                apply({**final, "translation": zh})
            if stop.wait(1.2):
                return
