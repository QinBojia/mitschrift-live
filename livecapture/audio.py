"""Audio sources: WASAPI loopback (what the PC plays), microphone, or a WAV file replay.

Every source delivers 16 kHz mono float32 blocks to an `on_audio` callback.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio
import soundfile as sf
import soxr

from . import SAMPLE_RATE as SR

OnAudio = Callable[[np.ndarray], None]


class AudioSystem:
    """Thin wrapper around one PyAudio (WASAPI) instance."""

    def __init__(self) -> None:
        self.pa = pyaudio.PyAudio()
        self._api = self.pa.get_host_api_info_by_type(pyaudio.paWASAPI)

    def close(self) -> None:
        self.pa.terminate()

    def loopback_devices(self) -> list[dict]:
        return list(self.pa.get_loopback_device_info_generator())

    def input_devices(self) -> list[dict]:
        devs = (self.pa.get_device_info_by_index(i) for i in range(self.pa.get_device_count()))
        return [
            d for d in devs
            if d["hostApi"] == self._api["index"] and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")
        ]

    def default_loopback(self) -> dict:
        return self.pa.get_default_wasapi_loopback()

    def default_input(self) -> dict:
        return self.pa.get_device_info_by_index(self._api["defaultInputDevice"])

    def find(self, kind: str, query: str | None) -> dict:
        """kind: 'loopback' | 'mic'. query: device index or name substring (None = system default)."""
        if query is None:
            return self.default_loopback() if kind == "loopback" else self.default_input()
        devs = self.loopback_devices() if kind == "loopback" else self.input_devices()
        for d in devs:
            if str(d["index"]) == str(query) or str(query).lower() in d["name"].lower():
                return d
        raise LookupError(f"no {kind} device matching {query!r}; run with --list-devices")


class LiveSource:
    def __init__(self, system: AudioSystem, kind: str, device: str | None = None):
        self.system, self.kind = system, kind
        self.info = system.find(kind, device)
        self.name = self.info["name"]
        self.rate = int(self.info["defaultSampleRate"])
        self.channels = max(1, int(self.info["maxInputChannels"]))
        self._stream = None
        self.overflows = 0

    def start(self, on_audio: OnAudio) -> None:
        rs = soxr.ResampleStream(self.rate, SR, 1, dtype="float32") if self.rate != SR else None
        ch = self.channels

        def callback(in_data, _frames, _time_info, status):
            if status:
                self.overflows += 1
            x = np.frombuffer(in_data, dtype=np.float32)
            x = x.reshape(-1, ch).mean(axis=1) if ch > 1 else x.copy()
            y = rs.resample_chunk(x) if rs else x
            if len(y):
                on_audio(np.asarray(y, dtype=np.float32))
            return None, pyaudio.paContinue

        self._stream = self.system.pa.open(
            format=pyaudio.paFloat32,
            channels=ch,
            rate=self.rate,
            input=True,
            input_device_index=self.info["index"],
            frames_per_buffer=self.rate // 50,  # 20 ms
            stream_callback=callback,
        )

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

    def describe(self) -> str:
        return f"{self.name} ({self.rate} Hz, {self.channels} ch)"


class FileSource:
    """Replays a WAV/FLAC file in (sped-up) real time, then 1.5 s of silence to flush."""

    def __init__(self, path: str | Path, speed: float = 1.0):
        audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
        self.audio = soxr.resample(audio, sr, SR).astype(np.float32) if sr != SR else audio
        self.speed = speed
        self.name = Path(path).name
        self.done = threading.Event()

    def start(self, on_audio: OnAudio) -> None:
        def run() -> None:
            block = SR // 50
            data = np.concatenate([self.audio, np.zeros(int(1.5 * SR), dtype=np.float32)])
            t0 = time.monotonic()
            for i in range(0, len(data), block):
                on_audio(data[i : i + block])
                wait = t0 + (i + block) / SR / self.speed - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
            self.done.set()

        threading.Thread(target=run, daemon=True, name=f"file-{self.name}").start()

    def stop(self) -> None:
        pass

    def describe(self) -> str:
        return f"{self.name} ({len(self.audio) / SR:.1f}s, x{self.speed:g})"


def list_devices() -> str:
    system = AudioSystem()
    try:
        lines = ["System audio (loopback):"]
        default_lb = system.default_loopback()["index"]
        for d in system.loopback_devices():
            mark = "*" if d["index"] == default_lb else " "
            lines.append(f"  {mark} [{d['index']}] {d['name']}  {int(d['defaultSampleRate'])} Hz")
        lines.append("Microphones:")
        default_in = system.default_input()["index"]
        for d in system.input_devices():
            mark = "*" if d["index"] == default_in else " "
            lines.append(f"  {mark} [{d['index']}] {d['name']}  {int(d['defaultSampleRate'])} Hz")
        lines.append("(* = Windows default)")
        return "\n".join(lines)
    finally:
        system.close()
