"""Streaming Silero VAD (ONNX): one speech probability per 512-sample frame (32 ms @ 16 kHz)."""

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
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._ctx = np.zeros(_CONTEXT, dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        x = np.concatenate([self._ctx, frame]).astype(np.float32)[None]
        out, self._state = self._sess.run(["output", "stateN"], {"input": x, "state": self._state, "sr": self._sr})
        self._ctx = frame[-_CONTEXT:]
        return float(out[0, 0])
