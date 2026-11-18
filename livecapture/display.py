"""Terminal views: a live rich view (finished lines scroll up with their translation,
active ones update in place) and a plain line printer for tests/logging."""

from __future__ import annotations

import threading
import time

from rich.console import Console, Group
from rich.text import Text

from . import STREAM_LABELS
from .segmenter import Caption
from .stt import SttEngine, is_cjk
from .translate import PENDING, Translator

_COLORS = {"other": "cyan", "me": "green"}
_INDENT = " " * 13  # under the text column: "HH:MM:SS " + label


def _caption_text(c: Caption) -> Text:
    t = Text()
    t.append(time.strftime("%H:%M:%S ", time.localtime(c.start)), style="dim")
    t.append(f"{STREAM_LABELS.get(c.stream, c.stream)}  ", style=f"bold {_COLORS.get(c.stream, 'white')}")
    t.append(c.committed)
    if c.tentative:
        cjk = c.committed and (is_cjk(c.committed[-1]) or is_cjk(c.tentative[0]))
        t.append(("" if not c.committed or cjk else " ") + c.tentative, style="dim italic")
    if c.translation == PENDING:
        t.append(f"\n{_INDENT}↳ 翻译中…", style="dim")
    elif c.translation:
        t.append(f"\n{_INDENT}↳ {c.translation}", style="bold yellow")
    return t


def _meter(db: float, width: int = 12) -> str:
    filled = int(max(0.0, min(1.0, (db + 60) / 60)) * width)
    return "█" * filled + "░" * (width - filled)


class LiveView:
    def __init__(self, console: Console, engine: SttEngine, sources: dict[str, str],
                 translator: Translator | None = None):
        self.console, self.engine, self.sources, self.translator = console, engine, sources, translator
        self._lock = threading.Lock()
        self._active: dict[tuple[str, int], Caption] = {}
        self._levels: dict[str, float] = {}
        self.lags: dict[str, float] = {}

    def on_caption(self, c: Caption) -> None:
        # Never print while holding self._lock: console.print re-renders the Live region,
        # which calls __rich__ -> self._lock again (self-deadlock; froze the UI after the
        # first finished sentence), and rich's own locks would invert with ours.
        line = None
        with self._lock:
            key = (c.stream, c.utt_id)
            if c.final:
                self._active.pop(key, None)
                if c.committed:
                    line = _caption_text(c)
            elif c.text:
                self._active[key] = c
        if line is not None:
            self.console.print(line)

    def on_level(self, stream: str, db: float) -> None:
        prev = self._levels.get(stream, -100.0)
        self._levels[stream] = max(db, prev - 1.5)  # fast attack, slow decay

    def __rich__(self) -> Group:
        stats = self.engine.stats()
        asr = f"识别 {stats[0]:.0f} ms" if stats else "识别 -"
        head = Text()
        head.append(f" {self.engine.kind.upper()} ", style="bold black on yellow")
        head.append(f" {asr}   ", style="dim")
        tr = self.translator
        if tr is not None:
            if tr.error:
                head.append(f"{tr.error}   ", style="bold red")
            else:
                ts = tr.stats()
                head.append(f"翻译→{tr.target} {getattr(tr.backend, 'model', '')} "
                            f"{f'{ts[0]:.0f} ms' if ts else '-'}   ", style="dim")
        for s in self.sources:
            db = self._levels.get(s, -100.0)
            head.append(f"{STREAM_LABELS.get(s, s)} ", style=_COLORS.get(s, "white"))
            head.append(_meter(db), style=_COLORS.get(s, "white"))
            lag = self.lags.get(s, 0.0)
            head.append(f" {db:>4.0f}dB" + (f" 积压{lag:.1f}s" if lag > 0.5 else "") + "   ", style="dim")
        head.append("Ctrl+C 退出", style="dim")
        with self._lock:
            active = sorted(self._active.values(), key=lambda c: c.start)
        return Group(*[_caption_text(c) for c in active], Text("─" * 40, style="dim"), head)


class PlainPrinter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.finals: list[Caption] = []

    def on_caption(self, c: Caption) -> None:
        if c.final and c.committed:
            with self._lock:
                self.finals.append(c)
                print(f"[{time.strftime('%H:%M:%S', time.localtime(c.start))}] "
                      f"{STREAM_LABELS.get(c.stream, c.stream)}{'[zh]' if c.lang == 'zh' else ''}: {c.committed}",
                      flush=True)
                if c.translation:
                    print(f"    → {c.translation}", flush=True)
