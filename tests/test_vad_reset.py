"""The VAD's second state starts afresh after a while without speech (SegmenterConfig.vad_reset):
a state gets used to long music or noise and then misses speech over it."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture.segmenter import SR, SegmenterConfig, StreamProcessor  # noqa: E402


class FakeVad:
    def __init__(self, p: float):
        self.p, self.frames, self.resets = p, 0, []

    def __call__(self, frame) -> float:
        self.frames += 1
        return self.p

    def refresh(self) -> None:
        self.resets.append(self.frames)


def resets(p: float, seconds: float, **cfg) -> list[int]:
    vad = FakeVad(p)
    proc = StreamProcessor("other", None, vad, SegmenterConfig(**cfg), lambda c: None, live=False)
    proc._ingest(np.zeros(int(seconds * SR), np.float32))  # noqa: SLF001
    return vad.resets


def test_reset_every_few_seconds_without_speech():
    assert len(resets(0.0, 10.0)) == 3  # after 3, 6 and 9 s
    assert resets(0.0, 10.0, vad_reset=0) == []  # switched off


def test_no_reset_while_it_might_be_speech():
    assert resets(0.4, 10.0) == []  # between the end and the start threshold


def test_the_long_running_state_survives_a_refresh():
    import pytest

    from livecapture import models
    from livecapture.vad import FRAME, SileroVad

    if not models.SILERO_PATH.exists():
        pytest.skip("Silero model not downloaded (the tests don't download models)")
    vad = SileroVad(models.SILERO_PATH)
    rng = np.random.default_rng(0)
    for _ in range(50):
        vad(rng.standard_normal(FRAME).astype(np.float32) * 0.05)
    kept = vad._state[:, 0].copy()  # noqa: SLF001
    vad.refresh()
    assert np.array_equal(vad._state[:, 0], kept) and not vad._state[:, 1].any()  # noqa: SLF001
