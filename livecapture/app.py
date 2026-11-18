"""Desktop shell: main window (pywebview / Edge WebView2) + floating caption bar.

The caption bar (caption_bar.py) is drawn natively with per-pixel alpha, so its text and
background have separate opacities - a WebView2 window can only be see-through as a
whole (bench/transparency_probe.py). It never takes focus (clicking it leaves Zoom
focused) and stays out of the taskbar. "Click-through" makes mouse input fall through to
the window below; a global hotkey (Ctrl+Alt+L, ...) toggles it from anywhere, since a
click-through window can't be clicked to turn it off.

  python -m livecapture.app             live captions (system audio + mic) with translation
  python -m livecapture.app --demo      UI with scripted demo data (no audio / models)
"""

from __future__ import annotations

import argparse
import ctypes
import functools
import http.server
import json
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

from . import APP_NAME, APP_NAME_ZH, __version__, logs, models
from .edition import EDITION

# before the heavy imports below: under pythonw.exe there is no console to write to
LOG_FILE = logs.setup(models.APP_DIR, __version__, EDITION.name)

import webview  # noqa: E402

from .caption_bar import CaptionBar, demo_feed  # noqa: E402
from .recorder import DEFAULT_DIR as RECORD_DIR  # noqa: E402
from .server import Backend  # noqa: E402
from .tray import Tray, save_app_icon  # noqa: E402

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
STATE_FILE = models.APP_DIR / "ui.json"

user32 = ctypes.windll.user32
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
WM_HOTKEY = 0x0312
HOTKEYS = [("Ctrl+Alt+L", MOD_CONTROL | MOD_ALT, ord("L")),  # L = lock (click-through)
           ("Ctrl+Alt+C", MOD_CONTROL | MOD_ALT, ord("C")),
           ("Ctrl+Alt+F12", MOD_CONTROL | MOD_ALT, 0x7B)]


# ---------------------------------------------------------------- helpers

class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep the console clean
        pass

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")  # always load the current UI files
        super().end_headers()


def serve_ui() -> int:
    """Serve ui/ on an ephemeral localhost port (ES modules need http://, not file://)."""
    handler = functools.partial(_QuietHandler, directory=str(UI_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="ui-http").start()
    return server.server_address[1]


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- JS API

class Api:
    """Exposed to both pages as window.pywebview.api (underscore attributes stay private)."""

    def __init__(self, app: DesktopApp):
        self._app = app

    def toggle_overlay(self) -> bool:
        return self._app.toggle_overlay()

    def set_click_through(self, on: bool) -> bool:
        return self._app.set_click_through(bool(on))

    def set_bar_pref(self, key: str, value) -> None:
        """Caption bar look: font (px), text_alpha / bg_alpha (0..1), translation (bool)."""
        self._app.bar.set_pref(str(key), value)

    def get_bar_prefs(self) -> dict:
        return dict(self._app.bar.prefs)

    def get_hotkey(self) -> str | None:
        return self._app.hotkey

    def open_recordings(self, path: str | None = None) -> None:
        """Open a recording folder in Explorer (only inside the recordings directory)."""
        root = RECORD_DIR.resolve()
        target = Path(path).resolve() if path else root
        if target != root and root not in target.parents:
            target = root
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(target)  # noqa: S606 - opens a folder we created, in Explorer

    def save_text(self, text: str, fmt: str) -> str | None:
        name = time.strftime(f"mitschrift-live-%Y%m%d-%H%M.{fmt}")
        result = self._app.main.create_file_dialog(webview.FileDialog.SAVE, save_filename=name)
        if not result:
            return None
        path = result if isinstance(result, str) else result[0]
        Path(path).write_text(text, encoding="utf-8-sig" if fmt == "txt" else "utf-8")
        return path


# ---------------------------------------------------------------- app

class DesktopApp:
    def __init__(self, backend: Backend | None):
        """backend=None: demo mode (scripted captions, no audio or models)."""
        self.state = load_state()
        self.backend = backend
        port = serve_ui()
        base = f"http://127.0.0.1:{port}"
        if backend:
            ws_port = backend.serve(ui_origin=base)
            query = f"?app=1&port={ws_port}&token={backend.token}"
        else:
            query = "?demo&app=1"
        api = Api(self)
        m = self.state.get("main", {})
        self.main = webview.create_window(
            f"{APP_NAME_ZH} {APP_NAME}", f"{base}/main.html{query}", js_api=api,
            width=m.get("w", 1060), height=m.get("h", 820), x=m.get("x"), y=m.get("y"),
            min_size=(560, 420), background_color="#111318",
        )
        # opaque by default (the user didn't want windows behind showing through); text and
        # background opacity can now be set apart in the settings drawer
        self.bar = CaptionBar(self.state.get("bar_prefs", {}), self.state.get("bar"),
                              on_prefs=self._on_bar_prefs, on_command=self._on_bar_command)
        self.overlay_visible = True
        self.click_through = False
        self.hotkey: str | None = None
        self._demo_stop = threading.Event()
        self.main.events.moved += lambda x, y: self._remember("main", x=x, y=y)
        self.main.events.resized += lambda w, h: self._remember("main", w=w, h=h)
        self.main.events.closing += self._on_main_closing
        self.main.events.closed += self._on_main_closed
        self._quitting = False
        self._told_about_tray = bool(self.state.get("told_about_tray"))
        self.tray = Tray(self)

    def _remember(self, key: str, **kw) -> None:
        self.state.setdefault(key, {}).update(kw)
        save_state(self.state)

    def _on_bar_prefs(self, prefs: dict, geometry: dict | None) -> None:
        """The caption bar changed (any thread): save it, and keep the drawer sliders in step."""
        self.state["bar_prefs"] = prefs
        if geometry:
            self.state["bar"] = geometry
        save_state(self.state)
        js = f"window.__setBarPrefs && window.__setBarPrefs({json.dumps(prefs)})"
        # evaluate_js waits for the page: never block the bar's window thread on it
        threading.Thread(target=self._js, args=(self.main, js), daemon=True).start()

    def _on_bar_command(self, cmd: str) -> None:
        if cmd == "lock":
            self.set_click_through(True)
        elif cmd == "hide":
            self.toggle_overlay()

    @staticmethod
    def _js(window: webview.Window, js: str) -> None:
        try:
            window.evaluate_js(js)
        except Exception:  # noqa: BLE001 - page may still be loading
            pass

    def start_bar(self) -> None:
        self.bar.start()
        if self.backend:
            self.backend.listeners.append(self.bar.apply)
        else:  # --demo: scripted captions
            threading.Thread(target=demo_feed, args=(self.bar.apply, self._demo_stop), daemon=True).start()

    def _on_main_closing(self) -> bool:
        """The ✕ hides to the tray (captions keep running); quit from the tray menu."""
        if self._quitting:
            return True
        self.main.hide()
        if not self._told_about_tray:
            self._told_about_tray = True
            self.state["told_about_tray"] = True
            save_state(self.state)
            self.tray.notify(f"{APP_NAME_ZH}还在托盘里运行。右键托盘图标可以退出。")
        return False  # cancel the close

    def _on_main_closed(self) -> None:
        self._demo_stop.set()
        if self.backend:
            self.backend.shutdown()
        self.tray.stop()
        self.bar.destroy()

    def show_main(self) -> None:
        self.main.show()
        self.main.restore()

    def quit(self) -> None:
        self._quitting = True
        self.main.destroy()  # -> _on_main_closed: backend, tray and the caption bar go too

    def toggle_overlay(self) -> bool:
        self.overlay_visible = not self.overlay_visible
        self.bar.set_visible(self.overlay_visible)
        return self.overlay_visible

    def set_click_through(self, on: bool) -> bool:
        self.bar.set_click_through(on)
        self.click_through = on
        js = f"window.__setLock && window.__setLock({str(on).lower()})"
        threading.Thread(target=self._js, args=(self.main, js), daemon=True).start()
        return on

    def hotkey_loop(self) -> None:
        """Global hotkey: toggle click-through (show the overlay first if hidden).

        Tries a few combinations: one may already be taken by another program (Ctrl+Alt+S
        was, on the developer's machine). The one that registered is shown in the UI tooltips.
        """
        for name, mods, vk in HOTKEYS:
            if user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
                self.hotkey = name
                break
        else:
            print("全局快捷键全部被占用，鼠标穿透只能在主窗口设置里关闭", flush=True)
            return
        # the page may still be loading; it also asks via get_hotkey()
        self._js(self.main, f"window.__setHotkey && window.__setHotkey({json.dumps(self.hotkey)})")
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                if not self.overlay_visible:
                    self.toggle_overlay()
                self.set_click_through(not self.click_through)


def main() -> None:
    ap = argparse.ArgumentParser(prog="livecapture.app")
    ap.add_argument("--demo", action="store_true", help="scripted demo data, no audio or models")
    ap.add_argument("--autostart", action="store_true", help="start capturing right away")
    ap.add_argument("--debug", action="store_true", help="enable WebView2 dev tools")
    args = ap.parse_args()
    # Own taskbar identity (not grouped with other python.exe windows) + our logo
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LiveCapture.Desktop")
    models.APP_DIR.mkdir(parents=True, exist_ok=True)
    icon = save_app_icon(models.APP_DIR / "live-capture.ico")
    backend = None if args.demo else Backend()
    app = DesktopApp(backend)

    def on_start() -> None:
        app.start_bar()
        threading.Thread(target=app.hotkey_loop, daemon=True, name="hotkey").start()
        app.tray.start()
        if backend:
            backend.preload()  # load models while the user looks at the window
            if args.autostart:
                backend.request_start()

    webview.start(on_start, debug=args.debug, icon=icon)


if __name__ == "__main__":
    main()
