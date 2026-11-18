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
# Full-int8 encoder for the NPU, produced by bench/npu_quantize.py
PARAKEET_OV_INT8 = MODELS_DIR / "parakeet-ov" / "encoder-int8q.xml"
# fp16 encoder for DirectML GPUs (AMD / Intel integrated graphics), produced by bench/to_fp16.py
PARAKEET_FP16 = PARAKEET_DIR / "encoder-model.fp16.onnx"

SILERO_REPO = "istupakov/silero-vad-onnx"
SILERO_PATH = MODELS_DIR / "silero-vad" / "silero_vad.onnx"

# Chinese (+en/ja/ko/yue) ASR with built-in language ID
# (the 2025-09-09 build is ASLP-lab's Cantonese fine-tune; this is the original FunAudioLLM model)
SENSEVOICE_REPO = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
SENSEVOICE_DIR = MODELS_DIR / "sensevoice-small-2024-07-17"


def _ensure(repo: str, local_dir: Path, files: list[str]) -> Path:
    missing = [f for f in files if not (local_dir / f).exists()]
    if missing:
        from huggingface_hub import hf_hub_download

        for f in missing:
            print(f"downloading {repo}/{f} ...")
            hf_hub_download(repo, f, local_dir=local_dir)
    return local_dir


def ensure_parakeet(quantization: str | None) -> Path:
    common = ["config.json", "vocab.txt", "nemo128.onnx"]
    if quantization == "int8":
        files = ["encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"]
    else:
        files = ["encoder-model.onnx", "encoder-model.onnx.data", "decoder_joint-model.onnx"]
    return _ensure(PARAKEET_REPO, PARAKEET_DIR, common + files)


def ensure_sensevoice() -> Path:
    return _ensure(SENSEVOICE_REPO, SENSEVOICE_DIR, ["model.int8.onnx", "tokens.txt"])


def ensure_silero() -> Path:
    _ensure(SILERO_REPO, SILERO_PATH.parent, [SILERO_PATH.name])
    return SILERO_PATH
