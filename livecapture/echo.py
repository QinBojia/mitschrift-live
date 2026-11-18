"""Keeping the far end out of the microphone stream ("我").

Without headphones the mic hears the speakers, so everything the other side says shows
up twice. Two layers:

1. EchoCanceller - WebRTC audio processing (AEC3 + noise suppression + high-pass,
   via livekit.rtc), with the system-audio loopback as the far-end reference. Removes
   the echo acoustically while keeping your own voice, including during double-talk.
2. EchoFilter - text backstop for residual echo (e.g. before the AEC has converged):
   a mic caption that overlaps in time with far-end speech and mostly repeats its
   words is hidden.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import replace
from difflib import SequenceMatcher

import numpy as np

from . import SAMPLE_RATE as SR
from .segmenter import Caption

_FRAME = SR // 100  # WebRTC APM works on 10 ms frames


class EchoCanceller:
    def __init__(self, noise_suppression: bool = True):
        from livekit import rtc

        self._rtc = rtc
        self._apm = rtc.AudioProcessingModule(
            echo_cancellation=True, noise_suppression=noise_suppression, high_pass_filter=True,
            auto_gain_control=False,
        )
        self._lock = threading.Lock()
        self._ref = np.zeros(0, dtype=np.float32)
        self._cap = np.zeros(0, dtype=np.float32)

    def _frame(self, x: np.ndarray):
        pcm = (np.clip(x, -1.0, 1.0) * 32767).astype(np.int16)
        return self._rtc.AudioFrame(pcm.tobytes(), SR, 1, _FRAME)

    def push_reference(self, block: np.ndarray) -> None:
        """Far-end audio (what the speakers play), 16 kHz mono float32."""
        with self._lock:
            self._ref = np.concatenate([self._ref, block])
            n = len(self._ref) // _FRAME
            for i in range(n):
                self._apm.process_reverse_stream(self._frame(self._ref[i * _FRAME : (i + 1) * _FRAME]))
            self._ref = self._ref[n * _FRAME :]

    def process_capture(self, block: np.ndarray) -> np.ndarray:
        """Microphone audio in, echo-cancelled audio out (may lag by < 10 ms)."""
        with self._lock:
            self._cap = np.concatenate([self._cap, block])
            n = len(self._cap) // _FRAME
            out = []
            for i in range(n):
                f = self._frame(self._cap[i * _FRAME : (i + 1) * _FRAME])
                self._apm.process_stream(f)
                out.append(np.frombuffer(f.data, dtype=np.int16).astype(np.float32) / 32768)
            self._cap = self._cap[n * _FRAME :]
        return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


_TOKEN = re.compile(r"[㐀-鿿豈-﫿]|[^\W_㐀-鿿豈-﫿]+")


def _words(text: str) -> list[str]:
    """Latin words, and single characters for Chinese (which has no spaces)."""
    return _TOKEN.findall(text.lower())


def _similar(a: str, b: str) -> bool:
    if a == b:
        return True
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.75


def containment(mine: list[str], theirs: list[str]) -> float:
    """Fraction of `mine` words that (fuzzily) occur in `theirs`."""
    if not mine:
        return 0.0
    their_set = set(theirs)
    hits = sum(1 for w in mine if w in their_set or any(_similar(w, o) for o in theirs))
    return hits / len(mine)


class EchoFilter:
    """Sits between the stream processors and the display; hides echoed mic captions."""

    def __init__(self, sink: Callable[[Caption], None], *, min_overlap: float = 0.5,
                 min_containment: float = 0.5, min_words: int = 3, history: float = 30.0):
        self.sink = sink
        self.min_overlap, self.min_containment, self.min_words = min_overlap, min_containment, min_words
        self.history = history
        self._lock = threading.Lock()
        self._other: dict[int, Caption] = {}
        self._hidden: set[int] = set()
        self.suppressed = 0  # finished mic utterances dropped as echo

    def on_caption(self, c: Caption) -> None:
        with self._lock:
            if c.stream == "other":
                self._other[c.utt_id] = c
                cutoff = c.end - self.history  # caption timeline, not wall clock
                self._other = {k: v for k, v in self._other.items() if v.end >= cutoff or not v.final}
                out = c
            elif c.stream == "me" and self._is_echo(c):
                if c.final:
                    self.suppressed += 1
                    self._hidden.discard(c.utt_id)
                elif c.utt_id in self._hidden:
                    return
                else:
                    self._hidden.add(c.utt_id)
                out = replace(c, committed="", tentative="", final=True)  # removes the line
            else:
                self._hidden.discard(c.utt_id)
                out = c
        self.sink(out)

    def _is_echo(self, c: Caption) -> bool:
        mine = _words(c.text)
        if len(mine) < self.min_words or c.end <= c.start:
            return False
        overlap, theirs = 0.0, []
        for o in self._other.values():
            ov = min(c.end, o.end + 0.5) - max(c.start, o.start - 0.5)  # small slack for timing jitter
            if ov > 0:
                overlap += ov
                theirs += _words(o.text)
        if overlap / (c.end - c.start) < self.min_overlap:
            return False
        return containment(mine, theirs) >= self.min_containment
