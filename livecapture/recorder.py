"""Session recording: 16 kHz mono MP3 per stream + finished captions (with translation) as JSONL,
in a folder of its own named after the start time (`<date>-<time>`, `-2`, ... if taken).

captions.jsonl: one finished caption per line - the Caption fields (stream, utt_id, start/end as
Unix times, committed text, lang "zh" or null, translation) plus whatever the caller adds (the app:
session, who, src) and `offset`: seconds into the audio files where the caption starts (negative
for captions from before the recording started; the app writes those first). meta.json: exact
start time (Unix and local), version and settings when it started, events while it ran (capture
paused / started, a side switched, translation target changed, each with its offset), end and
duration.

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

from . import APP_NAME, SAMPLE_RATE, __version__
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


def _new_dir(out_dir: Path, stamp: str) -> Path:
    """A folder that didn't exist yet: two recordings started within one second don't share one."""
    for n in range(1, 1000):
        d = out_dir / (stamp if n == 1 else f"{stamp}-{n}")
        try:
            d.mkdir(parents=True)
            return d
        except FileExistsError:
            continue
    raise FileExistsError(out_dir / stamp)


class Recorder:
    def __init__(self, out_dir: str | Path, streams: list[str], align: bool = False, meta: dict | None = None):
        """meta: extra fields for meta.json (the app: edition and settings)."""
        self.started = time.time()
        self._t0 = time.monotonic()
        self.dir = _new_dir(Path(out_dir), time.strftime("%Y%m%d-%H%M%S", time.localtime(self.started)))
        self._streams, self._align = set(streams), align
        self._wavs: dict[str, sf.SoundFile] = {}
        self._jsonl = (self.dir / "captions.jsonl").open("w", encoding="utf-8")
        self._lock = threading.Lock()
        self._closed = False
        local = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started)) + f".{int(self.started % 1 * 1000):03d}"
        self.meta = {"app": APP_NAME, "version": __version__, "started": round(self.started, 3),
                     "started_local": local, "captions_before": 0, **(meta or {}), "events": []}
        self._write_meta()

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
            self.write_caption(asdict(c))

    def write_caption(self, record: dict) -> None:
        """A finished caption (Caption fields + extras); gets its offset into the audio."""
        with self._lock:
            if self._closed:
                return
            line = {**record, "offset": round(record["start"] - self.started, 2)}
            self._jsonl.write(json.dumps(line, ensure_ascii=False) + "\n")
            self._jsonl.flush()

    def event(self, name: str, **data) -> None:
        """Something that changed while recording (pause, start, a side switched, ...), in meta.json."""
        with self._lock:
            if self._closed:
                return
            self.meta["events"].append({"offset": round(time.time() - self.started, 2), "event": name, **data})
            self._write_meta()

    def _write_meta(self) -> None:
        (self.dir / "meta.json").write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            ended = time.time()
            self.meta.update(ended=round(ended, 3), duration=round(ended - self.started, 2))
            self._write_meta()
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
