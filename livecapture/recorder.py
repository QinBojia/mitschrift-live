"""Session recording: 16 kHz WAV per stream + finished captions (with translation) as JSONL.

Useful for re-testing and for re-calibrating the int8 NPU model on real meeting audio.
Writes come from the audio threads while the UI may close the recorder at any time, so
everything goes through one lock and late writes after close() are dropped.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import soundfile as sf

from . import SAMPLE_RATE
from .segmenter import Caption

DEFAULT_DIR = Path.home() / "Documents" / "Mitschrift Live"


class Recorder:
    def __init__(self, out_dir: str | Path, streams: list[str]):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = Path(out_dir) / stamp
        self.dir.mkdir(parents=True, exist_ok=True)
        self._wavs = {
            s: sf.SoundFile(self.dir / f"{s}.wav", "w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16")
            for s in streams
        }
        self._jsonl = (self.dir / "captions.jsonl").open("w", encoding="utf-8")
        self._lock = threading.Lock()
        self._closed = False
        self.started = time.time()

    def on_audio(self, stream: str, block: np.ndarray) -> None:
        with self._lock:
            if not self._closed and stream in self._wavs:
                self._wavs[stream].write(block)

    def on_caption(self, c: Caption) -> None:
        if c.final and c.committed:
            with self._lock:
                if not self._closed:
                    self._jsonl.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
                    self._jsonl.flush()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for s, w in self._wavs.items():
                frames = w.frames
                w.close()
                if frames == 0:  # e.g. mic not used: don't leave an empty file behind
                    (self.dir / f"{s}.wav").unlink(missing_ok=True)
            self._jsonl.close()
