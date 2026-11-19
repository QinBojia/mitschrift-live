"""Session recording: 16 kHz mono MP3 per stream + finished captions (with translation) as JSONL.

MP3 at 64 kbps constant bitrate: ~29 MB per hour and stream instead of 115 MB as 16-bit WAV,
and plenty for speech at 16 kHz. Decoded, it has exactly the samples that were written, so
caption times still line up. Encoding costs ~2 ms per second of audio. Replaying a recording
(`--file`) recognizes slightly differently than live, because MP3 is lossy; a higher bitrate
doesn't change that (bench test 2026-11-19: 64-160 kbps all alike). If this soundfile /
libsndfile can't write MP3, the recorder falls back to WAV.

A file is opened when its side first delivers audio. With align=True (live capture) every
file follows the clock from the moment the recording started: time in which a side delivered
nothing - it was switched off, or nothing was playing yet - is filled with silence, so the
files of the two sides stay in step with each other and with the caption times.

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
MP3_KBPS = 64
ALIGN_SLACK = 0.5  # s a file may lag the clock before the gap is filled (capture jitter is far below)


def _mp3_level(kbps: int) -> float:
    """libsndfile's constant-bitrate compression level for 16-24 kHz audio (MPEG-2 Layer III):
    bitrate = 160 - level * (160 - 8) kbps."""
    return (160 - kbps) / 152


def open_audio(path_stem: Path) -> sf.SoundFile:
    """`<stem>.mp3` for writing, or `<stem>.wav` if MP3 isn't available."""
    mp3 = path_stem.with_name(path_stem.name + ".mp3")
    try:
        return sf.SoundFile(mp3, "w", samplerate=SAMPLE_RATE, channels=1, format="MP3",
                            bitrate_mode="CONSTANT", compression_level=_mp3_level(MP3_KBPS))
    except (sf.LibsndfileError, TypeError, ValueError):  # libsndfile < 1.1 / soundfile < 0.13
        mp3.unlink(missing_ok=True)
        return sf.SoundFile(path_stem.with_name(path_stem.name + ".wav"), "w", samplerate=SAMPLE_RATE,
                            channels=1, subtype="PCM_16")


class Recorder:
    def __init__(self, out_dir: str | Path, streams: list[str], align: bool = False):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.dir = Path(out_dir) / stamp
        self.dir.mkdir(parents=True, exist_ok=True)
        self._streams, self._align = set(streams), align
        self._wavs: dict[str, sf.SoundFile] = {}
        self._jsonl = (self.dir / "captions.jsonl").open("w", encoding="utf-8")
        self._lock = threading.Lock()
        self._closed = False
        self.started = time.time()
        self._t0 = time.monotonic()

    def on_audio(self, stream: str, block: np.ndarray) -> None:
        with self._lock:
            if self._closed or stream not in self._streams:
                return
            w = self._wavs.get(stream)
            if w is None:
                w = self._wavs[stream] = open_audio(self.dir / stream)
            if self._align:
                behind = int((time.monotonic() - self._t0) * SAMPLE_RATE) - w.frames - len(block)
                if behind > ALIGN_SLACK * SAMPLE_RATE:
                    w.write(np.zeros(behind, dtype=np.float32))
            w.write(block)

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
            for w in self._wavs.values():
                if self._align:  # a side that went quiet or was switched off: same end as the other file
                    behind = int((time.monotonic() - self._t0) * SAMPLE_RATE) - w.frames
                    if behind > 0:
                        w.write(np.zeros(behind, dtype=np.float32))
                frames = w.frames
                w.close()
                if frames == 0:  # e.g. mic not used: don't leave an empty file behind
                    Path(w.name).unlink(missing_ok=True)
            self._jsonl.close()
