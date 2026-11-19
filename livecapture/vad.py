"""Streaming Silero VAD (ONNX): one speech probability per 512-sample frame (32 ms @ 16 kHz).

Two copies of the model state run side by side (one batched call, about the cost of one):
one keeps its state for good, the other starts afresh whenever the caller says so
(refresh(), see SegmenterConfig.vad_reset). A long stretch of music or noise makes a state
deaf to speech over it - dialogue under 50 s of film score got p = 0.00, the same audio
with a state reset 5 s before it 0.7-1.0 - while a fresh state is a little slower to pick
up some speech the long-running one hears. The probability is the larger of the two.
"""

from pathlib import Path

import numpy as np
import onnxruntime as rt

from . import SAMPLE_RATE

FRAME = 512
_CONTEXT = 64


class SileroVad:
    def __init__(self, model_path: Path):
        so = rt.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self._sess = rt.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        self._sr = np.array([SAMPLE_RATE], dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 2, 128), dtype=np.float32)  # (layers, copy, units)
        self._ctx = np.zeros(_CONTEXT, dtype=np.float32)

    def refresh(self) -> None:
        """Start the second copy of the state afresh."""
        self._state[:, 1] = 0.0

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self._ctx, frame]).astype(np.float32)
        out, self._state = self._sess.run(["output", "stateN"],
                                          {"input": np.stack([x, x]), "state": self._state, "sr": self._sr})
        self._ctx = frame[-_CONTEXT:]
        return float(out.max())
