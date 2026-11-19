"""Model file locations; downloads missing files as real files (no HF-cache symlinks,
which onnxruntime >= 1.32 rejects for models with external weight data).

Not under %LOCALAPPDATA%: processes started from a packaged (MSIX) app's terminal,
such as the Claude desktop app, get AppData silently redirected to that package's
private folder, which caused a second full model download. ~/.livecapture is not
virtualized. Override with the LIVECAPTURE_HOME environment variable.
"""

import os
from pathlib import Path

from .edition import APP_ROOT

APP_DIR = Path(os.environ.get("LIVECAPTURE_HOME", Path.home() / ".livecapture"))  # settings, logs, caches
# A packaged build ships its models next to the code (APP_ROOT/models); a dev checkout
# downloads them into APP_DIR/models.
_BUNDLED = APP_ROOT / "models"
MODELS_DIR = _BUNDLED if _BUNDLED.is_dir() else APP_DIR / "models"
OV_CACHE_DIR = APP_DIR / "ov_cache"

PARAKEET_NAME = "nemo-parakeet-tdt-0.6b-v3"
PARAKEET_REPO = "istupakov/parakeet-tdt-0.6b-v3-onnx"
PARAKEET_DIR = MODELS_DIR / "parakeet-tdt-0.6b-v3-onnx"
# int8 encoder + decoder for CPUs, produced by bench/to_int8.py. Its encoder keeps the
# subsampling and depthwise convolutions in fp32: the ready-made int8 encoder quantizes them
# too and then drops whole passages (bench/to_int8.py has the numbers).
PARAKEET_CPU_DIR = MODELS_DIR / "parakeet-cpu"
# fp16 encoder for the Intel NPU and DirectML GPUs (AMD / Intel integrated graphics),
# produced by bench/to_fp16.py
PARAKEET_FP16 = PARAKEET_DIR / "encoder-model.fp16.onnx"
# Full-int8 encoder for the NPU (bench/npu_quantize.py). No longer preferred: on real
# recordings it made twice the errors of fp16 for 7 % less time. Installs up to 0.2.x have it.
PARAKEET_OV_INT8 = MODELS_DIR / "parakeet-ov" / "encoder-int8q.xml"

SILERO_REPO = "istupakov/silero-vad-onnx"
SILERO_PATH = MODELS_DIR / "silero-vad" / "silero_vad.onnx"

# Chinese (+en/ja/ko/yue) ASR with built-in language ID
# (the 2025-09-09 build is ASLP-lab's Cantonese fine-tune; this is the original FunAudioLLM model)
SENSEVOICE_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SENSEVOICE_DIR = MODELS_DIR / "sensevoice-small-2024-07-17"


HF_MIRROR = "https://hf-mirror.com"
_endpoint: list[str | None] = []  # cached: None = huggingface.co


def hub_endpoint() -> str | None:
    """Where to download models from: huggingface.co, or - when it can't be reached within a
    few seconds, as in mainland China - the hf-mirror.com mirror. HF_ENDPOINT, if set, wins
    (huggingface_hub reads it itself)."""
    if os.environ.get("HF_ENDPOINT"):
        return None
    if not _endpoint:
        import urllib.request

        try:
            urllib.request.urlopen(urllib.request.Request("https://huggingface.co", method="HEAD"), timeout=5)
            _endpoint.append(None)
        except OSError:
            print(f"huggingface.co not reachable, downloading from {HF_MIRROR}", flush=True)
            _endpoint.append(HF_MIRROR)
    return _endpoint[0]


def _ensure(repo: str, local_dir: Path, files: list[str]) -> Path:
    missing = [f for f in files if not (local_dir / f).exists()]
    if missing:
        from huggingface_hub import hf_hub_download

        endpoint = hub_endpoint()
        for f in missing:
            print(f"downloading {repo}/{f} ...", flush=True)
            hf_hub_download(repo, f, local_dir=local_dir, endpoint=endpoint)
    return local_dir


def ensure_parakeet(quantization: str | None) -> Path:
    common = ["config.json", "vocab.txt", "nemo128.onnx"]
    if quantization == "int8":
        files = ["encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"]
    else:
        files = ["encoder-model.onnx", "encoder-model.onnx.data", "decoder_joint-model.onnx"]
    return _ensure(PARAKEET_REPO, PARAKEET_DIR, common + files)


def parakeet_int8() -> tuple[Path, bool]:
    """(folder with an int8 encoder + decoder, whether it is our own encoder from
    bench/to_int8.py). Without that one: the ready-made int8 files, downloaded if missing."""
    if (PARAKEET_CPU_DIR / "encoder-model.int8.onnx").exists():
        return PARAKEET_CPU_DIR, True
    return ensure_parakeet("int8"), False


def npu_encoder() -> tuple[Path, str]:
    """(encoder file for the NPU, its precision). Anything unquantized first: the NPU runs
    fp32 files as fp16 too. The int8 IR only where nothing else is installed."""
    fp32 = PARAKEET_DIR / "encoder-model.onnx"
    if PARAKEET_FP16.exists():
        return PARAKEET_FP16, "fp16"
    if fp32.exists() and (PARAKEET_DIR / "encoder-model.onnx.data").exists():
        return fp32, "fp16"
    if PARAKEET_OV_INT8.exists():
        return PARAKEET_OV_INT8, "int8"
    return ensure_parakeet(None) / "encoder-model.onnx", "fp16"


def ensure_sensevoice() -> Path:
    return _ensure(SENSEVOICE_REPO, SENSEVOICE_DIR, ["model.int8.onnx", "tokens.txt"])


def ensure_silero() -> Path:
    _ensure(SILERO_REPO, SILERO_PATH.parent, [SILERO_PATH.name])
    return SILERO_PATH
