"""System tray icon for the desktop app (pystray, own thread).

Left click: show the main window. Right click: start/pause, windows, click-through,
translation target, quit. The icon gets a red dot while capturing.
"""

from __future__ import annotations

import ctypes
import threading
import time
from typing import TYPE_CHECKING

import pystray
from PIL import Image, ImageDraw

from .i18n import lang, tr

if TYPE_CHECKING:
    from .app import DesktopApp

TARGETS = [("中文", "zh"), ("English", "en"), ("Deutsch", "de"), (None, "off")]  # None: "off" in the UI language


def make_icon(running: bool, size: int = 64) -> Image.Image:
    """A caption box (two text lines) on a blue tile; red dot while capturing."""
    s = 256
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 28, 248, 228), radius=48, fill=(59, 130, 246, 255))
    d.rounded_rectangle((52, 92, 204, 120), radius=14, fill=(255, 255, 255, 255))
    d.rounded_rectangle((52, 144, 164, 172), radius=14, fill=(255, 255, 255, 215))
    if running:
        d.ellipse((168, 0, 256, 88), fill=(255, 255, 255, 255))
        d.ellipse((180, 12, 244, 76), fill=(239, 68, 68, 255))
    return img.resize((size, size), Image.LANCZOS)


def save_app_icon(path) -> str:
    """Write the logo as a multi-size .ico (taskbar, window title, Alt+Tab)."""
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    make_icon(False, 256).save(path, format="ICO", sizes=sizes)
    return str(path)


def dpi_aware_thread() -> None:
    """Make the calling thread per-monitor DPI aware (V2). Windows it creates - and the tray
    menu - are then drawn at the screen's real resolution instead of at 96 DPI and stretched
    (which looked blurry at 175 %)."""
    try:
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):  # older Windows: stay as before
        pass


class Tray:
    def __init__(self, app: DesktopApp):
        self.app = app
        self._icons = {False: make_icon(False), True: make_icon(True)}
        self._state: tuple | None = None
        self._stop = threading.Event()
        self.icon = pystray.Icon("live-capture", self._icons[False], tr("app"), self._menu())

    # ---- menu --------------------------------------------------------------------
    def _menu(self) -> pystray.Menu:
        app, b = self.app, self.app.backend
        item = pystray.MenuItem
        return pystray.Menu(
            item(lambda _: tr("tray.show"), lambda: app.show_main(), default=True),  # default = left click
            pystray.Menu.SEPARATOR,
            item(lambda _: tr("tray.pause") if self._running() else tr("tray.start"), lambda: self._toggle_run(),
                 enabled=lambda _: b is not None and not b.loading),
            item(lambda _: tr("tray.overlay"), lambda: app.toggle_overlay(), checked=lambda _: app.overlay_visible),
            item(lambda _: tr("tray.lock_hk", hk=app.hotkey) if app.hotkey else tr("tray.lock"),
                 lambda: app.set_click_through(not app.click_through), checked=lambda _: app.click_through),
            pystray.Menu.SEPARATOR,
            item(lambda _: tr("tray.src_other"), self._toggle_source("other"),
                 checked=lambda _: self._source_on("other"), enabled=lambda _: b is not None),
            item(lambda _: tr("tray.src_me"), self._toggle_source("me"),
                 checked=lambda _: self._source_on("me"), enabled=lambda _: b is not None),
            item(lambda _: tr("tray.target"), pystray.Menu(*[
                item(lambda _, s=label: s or tr("tray.target_off"), self._set_target(value), radio=True,
                     checked=lambda _, v=value: b is not None and b.settings.target == v,
                     enabled=lambda _: b is not None)
                for label, value in TARGETS
            ])),
            pystray.Menu.SEPARATOR,
            item(lambda _: tr("tray.quit"), lambda: app.quit()),
        )

    def _set_target(self, value: str):
        return lambda: self.app.backend and self.app.backend.request_set("target", value)

    def _source_on(self, k: str) -> bool:
        b = self.app.backend
        return bool(b and k in b.settings.sources)

    def _toggle_source(self, k: str):
        def toggle() -> None:
            b = self.app.backend
            if b:
                on = set(b.settings.sources) ^ {k}
                b.request_set("sources", [s for s in ("other", "me") if s in on])
        return toggle

    def _running(self) -> bool:
        b = self.app.backend
        return bool(b and b.pipeline and b.pipeline.running)

    def _toggle_run(self) -> None:
        b = self.app.backend
        if b:
            b.request_stop() if self._running() else b.request_start()

    # ---- lifecycle -----------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._run_icon, daemon=True, name="tray").start()
        threading.Thread(target=self._watch, daemon=True, name="tray-watch").start()

    def _run_icon(self) -> None:
        dpi_aware_thread()  # pystray creates its hidden window and the menu on this thread
        self.icon.run()

    def _watch(self) -> None:
        """Keep icon, tooltip and menu checkmarks in step with the app (UI changes included)."""
        while not self._stop.wait(0.5):
            b = self.app.backend
            running = self._running()
            state = (running, self.app.overlay_visible, self.app.click_through, self.app.hotkey,
                     b.settings.target if b else None, b.loading if b else None, lang(),
                     tuple(b.settings.sources) if b else None)
            if state == self._state:
                continue
            self._state = state
            try:
                self.icon.icon = self._icons[running]
                self.icon.title = f"{tr('app')} · " + (tr("tray.running") if running else (b.loading if b and b.loading else tr("tray.paused")))
                self.icon.update_menu()
            except Exception:  # noqa: BLE001 - icon not shown yet / shutting down
                pass

    def notify(self, message: str) -> None:
        try:
            self.icon.notify(message, tr("app"))
        except Exception:  # noqa: BLE001
            pass

    def stop(self) -> None:
        self._stop.set()
        try:
            self.icon.stop()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)
