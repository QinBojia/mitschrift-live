"""Speech-to-text engines: Parakeet-TDT-0.6B-v3 on an NVIDIA GPU, the Intel NPU, a DirectML GPU
(AMD / Intel integrated graphics) or the CPU.

Settings come from the benchmarks in bench/: ORT thread spinning off, cuDNN heuristic
algorithm search, int8 TDT decoder on CPU for the NPU/DirectML/CPU engines, an fp16 encoder
with a single static 10 s window on the NPU, and the same fp16 encoder fed one zero-padded
10 s window on DirectML (int8 is ~2.7x slower there, dynamic lengths ~3x slower). The CPU
encoder is int8 with the convolutions that don't survive quantization kept in fp32
(bench/to_int8.py); quantized encoders were measured against real recordings on 2026-11-19:
the ready-made int8 one loses whole passages, the full-int8 NPU one doubles the errors.
"""

from __future__ import annotations

import collections
import os
import platform
import statistics
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as rt

from . import SAMPLE_RATE, models

TOKEN_STEP = 0.08  # encoder frame = 8 x 10 ms

rt.set_default_logger_severity(3)  # hide the harmless "Memcpy nodes added" CUDA warning


@dataclass
class Word:
    text: str
    start: float  # onset of the first token, seconds from clip start
    last: float  # onset of the last token
    space: bool = True  # separated from the previous word by a space (False for CJK characters)

    @property
    def end(self) -> float:
        return self.last + TOKEN_STEP


@dataclass
class Transcript:
    text: str
    words: list[Word]
    lang: str | None = None  # "zh" etc. when known (SenseVoice); None for Parakeet (de/en/...)


def is_cjk(ch: str) -> bool:
    return "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿" or "　" <= ch <= "〿" or "＀" <= ch <= "￯"


def join_text(a: str, b: str) -> str:
    """Join two caption fragments; no space at a CJK boundary."""
    if not a or not b:
        return a or b
    sep = "" if is_cjk(a[-1]) or is_cjk(b[0]) else " "
    return a + sep + b


def words_from_tokens(tokens: list[str], times: list[float]) -> list[Word]:
    """Group SentencePiece tokens into words; a leading space starts a new word."""
    words: list[Word] = []
    for tok, t in zip(tokens, times, strict=False):
        if tok.startswith(" ") or not words:
            words.append(Word(tok.strip(), t, t))
        else:
            words[-1].text += tok
            words[-1].last = t
    return [w for w in words if w.text]


def words_text(words: list[Word]) -> str:
    return "".join((" " if i and w.space else "") + w.text for i, w in enumerate(words))


class _OvEncoder:
    """Stands in for the ORT encoder session; runs on an OpenVINO device with one static window."""

    def __init__(self, model_path: Path, device: str, window_s: float):
        import openvino as ov

        core = ov.Core()
        models.OV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        core.set_property({"CACHE_DIR": str(models.OV_CACHE_DIR)})
        model = core.read_model(str(model_path))
        self.frames = int(window_s * 100) + 8
        model.reshape({"audio_signal": [1, 128, self.frames], "length": [1]})
        self._req = core.compile_model(model, device, {"PERFORMANCE_HINT": "LATENCY"}).create_infer_request()
        self.device_name = core.get_property(device, "FULL_DEVICE_NAME")

    def run(self, _names, feed):
        feats, lens = feed["audio_signal"], feed["length"]
        t = feats.shape[2]
        if t > self.frames:
            raise ValueError(f"segment of {t} frames exceeds the NPU window ({self.frames})")
        padded = np.zeros((1, 128, self.frames), dtype=np.float32)
        padded[:, :, :t] = feats
        res = self._req.infer({"audio_signal": padded, "length": np.asarray(lens, dtype=np.int64)})
        enc_lens = np.asarray(res["encoded_lengths"]).astype(np.int64)
        out = np.asarray(res["outputs"])
        return [out[:, :, : int(enc_lens.max())], enc_lens]


class _DmlEncoder:
    """Stands in for the ORT encoder session; fp16 encoder on a DirectML GPU (AMD / Intel iGPU).

    Inputs are zero-padded to one fixed window: DirectML recompiles for every new input
    length (~0.7 s per call otherwise). The window is NOT declared as a static dimension
    (add_free_dimension_override_by_name) - with ORT 1.24 that produced wrong outputs.
    """

    def __init__(self, model_path: Path, device_id: int, window_s: float):
        if "DmlExecutionProvider" not in rt.get_available_providers():
            raise RuntimeError("this build of onnxruntime has no DirectML support")
        if not model_path.exists():
            raise FileNotFoundError(model_path)
        so = rt.SessionOptions()
        so.enable_mem_pattern = False  # required by the DirectML EP
        so.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
        so.add_free_dimension_override_by_name("audio_signal_dynamic_axes_1", 1)
        so.add_free_dimension_override_by_name("length_dynamic_axes_1", 1)
        self._sess = rt.InferenceSession(str(model_path), so,
                                         providers=[("DmlExecutionProvider", {"device_id": device_id})])
        if self._sess.get_providers()[0] != "DmlExecutionProvider":
            raise RuntimeError("DirectML could not take the encoder")
        self.frames = int(window_s * 100) + 8

    def run(self, _names, feed):
        feats = feed["audio_signal"]
        t = feats.shape[2]
        if t > self.frames:
            raise ValueError(f"segment of {t} frames exceeds the GPU window ({self.frames})")
        padded = np.zeros((1, 128, self.frames), dtype=np.float32)
        padded[:, :, :t] = feats
        out, lens = self._sess.run(["outputs", "encoded_lengths"], {"audio_signal": padded, "length": feed["length"]})
        return [out[:, :, : int(lens.max())], lens]


def _gpu_name() -> str:
    try:
        import pynvml

        pynvml.nvmlInit()
        return pynvml.nvmlDeviceGetName(pynvml.nvmlDeviceGetHandleByIndex(0))
    except Exception:  # noqa: BLE001
        return "CUDA GPU"


class SttEngine:
    KINDS = ("cuda", "npu", "dml", "cpu")

    def __init__(self, kind: str = "cuda", *, threads: int | None = None):
        import onnx_asr

        if kind not in self.KINDS:
            raise ValueError(f"engine must be one of {self.KINDS}")
        self.kind = kind
        so = rt.SessionOptions()
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        so.add_session_config_entry("session.inter_op.allow_spinning", "0")
        cpu = ["CPUExecutionProvider"]

        if kind == "cuda":
            try:
                rt.preload_dlls()  # CUDA / cuDNN DLLs from the nvidia-* pip packages
            except Exception:  # noqa: BLE001
                pass
            so.intra_op_num_threads = threads or 4
            providers = [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"}), *cpu]
            model = onnx_asr.load_model(
                models.PARAKEET_NAME, models.ensure_parakeet(None), providers=providers, sess_options=so
            )
            self.max_seconds = 30.0
            self.device = _gpu_name()
        elif kind == "cpu":
            # about one thread per physical core (logical/2), 2..8: leaves room for Zoom
            so.intra_op_num_threads = threads or max(2, min(8, (os.cpu_count() or 4) // 2))
            folder, own = models.parakeet_int8()
            model = onnx_asr.load_model(
                models.PARAKEET_NAME, folder, quantization="int8", providers=cpu, sess_options=so,
            )
            self.max_seconds = 30.0
            # "old encoder": the ready-made int8 file of installs up to 0.2.x (drops passages)
            self.device = f"{platform.processor() or 'CPU'} ({'int8' if own else 'int8, old encoder'})"
        elif kind == "dml":
            from . import dml

            adapter = dml.pick()
            if adapter is None:
                raise RuntimeError("no DirectX 12 GPU found")
            so.intra_op_num_threads = threads or 2  # preprocessor + TDT decoder only
            model = onnx_asr.load_model(
                models.PARAKEET_NAME, models.parakeet_int8()[0], quantization="int8",
                providers=cpu, sess_options=so,
            )
            self.max_seconds = 10.0
            model.asr._encoder = _DmlEncoder(models.PARAKEET_FP16, adapter.index, self.max_seconds)  # noqa: SLF001
            self.device = f"{adapter.name} (DirectML fp16)"
        else:  # npu
            so.intra_op_num_threads = threads or 2
            model = onnx_asr.load_model(
                models.PARAKEET_NAME, models.parakeet_int8()[0], quantization="int8",
                providers=cpu, sess_options=so,
            )
            enc_path, precision = models.npu_encoder()
            self.max_seconds = 10.0
            enc = _OvEncoder(enc_path, "NPU", self.max_seconds)
            model.asr._encoder = enc  # noqa: SLF001 - swap the ORT CPU encoder for the NPU one
            self.device = f"{enc.device_name} ({precision})"

        self._model = model.with_timestamps()
        self._lock = threading.Lock()
        self.timings: collections.deque[float] = collections.deque(maxlen=200)
        self.calls = 0  # transcribe() calls so far (for the speed log)
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32))  # warm-up
        self.timings.clear()
        self.calls = 0

    def transcribe(self, audio: np.ndarray) -> Transcript:
        if len(audio) < int(0.2 * SAMPLE_RATE):
            return Transcript("", [])
        if len(audio) > int(self.max_seconds * SAMPLE_RATE):
            raise ValueError(f"{len(audio) / SAMPLE_RATE:.1f}s exceeds engine limit {self.max_seconds}s")
        with self._lock:
            t = time.perf_counter()
            r = self._model.recognize(np.ascontiguousarray(audio, dtype=np.float32))
            self.timings.append((time.perf_counter() - t) * 1000)
            self.calls += 1
        return Transcript(r.text.strip(), words_from_tokens(r.tokens or [], r.timestamps or []))

    def stats(self) -> tuple[float, float] | None:
        """(mean, p95) transcribe time in ms over recent calls."""
        ts = sorted(self.timings.copy())  # copy is atomic; iterating while another thread appends raises
        if not ts:
            return None
        return statistics.mean(ts), ts[int(0.95 * (len(ts) - 1))]
