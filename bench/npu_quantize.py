"""Make int8 OpenVINO IR variants of the Parakeet encoder for NPU testing.

  int8w : weights-only INT8 (data-free, nncf.compress_weights) — compute stays fp16
  int8q : full post-training quantization (weights + activations), calibrated on
          bench/audio_calib (separate from the scored test clips)

Usage: python npu_quantize.py [--samples 200]
"""

import argparse
import time
from pathlib import Path

import nncf
import numpy as np
import openvino as ov
import soundfile as sf

import parakeet_bench as pb

FRAMES = 1008  # 10 s window + margin, matches npu_test.py
OUT_DIR = pb.DEFAULT_MODEL_DIR.parent / "parakeet-ov"


def calib_items(n: int, seed: int = 0) -> list[dict]:
    asr = pb.load(str(pb.DEFAULT_MODEL_DIR), "cpu", "int8", threads=8, spin=False)
    pre = asr.asr._preprocessor
    clips = [sf.read(p, dtype="float32")[0] for p in sorted((Path(__file__).parent / "audio_calib").glob("*.wav"))]
    rng = np.random.default_rng(seed)
    items = []
    for i in range(n):
        a = clips[i % len(clips)]
        L = min(int(rng.uniform(1.0, 10.0) * pb.SR), len(a))
        s = int(rng.integers(0, len(a) - L + 1))
        feats, lens = pre(a[None, s : s + L], np.array([L], dtype=np.int64))
        t = min(feats.shape[2], FRAMES)
        padded = np.zeros((1, 128, FRAMES), dtype=np.float32)
        padded[:, :, :t] = feats[:, :, :t]
        items.append({"audio_signal": padded, "length": np.minimum(lens, FRAMES).astype(np.int64)})
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=200)
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    core = ov.Core()
    src = pb.DEFAULT_MODEL_DIR / "encoder-model.onnx"

    t = time.perf_counter()
    m = core.read_model(str(src))
    m = nncf.compress_weights(m, mode=nncf.CompressWeightsMode.INT8_SYM)
    ov.save_model(m, OUT_DIR / "encoder-int8w.xml")
    print(f"int8w saved in {time.perf_counter() - t:.0f}s")
    del m

    items = calib_items(args.samples)
    print(f"{len(items)} calibration items ready")
    t = time.perf_counter()
    m = core.read_model(str(src))
    q = nncf.quantize(
        m,
        nncf.Dataset(items),
        model_type=nncf.ModelType.TRANSFORMER,
        preset=nncf.QuantizationPreset.MIXED,
        subset_size=len(items),
    )
    ov.save_model(q, OUT_DIR / "encoder-int8q.xml")
    print(f"int8q saved in {time.perf_counter() - t:.0f}s")

    for f in sorted(OUT_DIR.glob("*.bin")):
        print(f"  {f.name}: {f.stat().st_size / 2**20:.0f} MiB")


if __name__ == "__main__":
    main()
