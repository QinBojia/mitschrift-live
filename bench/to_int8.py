"""Make the int8 Parakeet encoder for CPUs: quantize the matrix multiplications and 1x1
convolutions, keep the subsampling and depthwise convolutions in fp32.

Usage: python to_int8.py   ->  ~/.livecapture/models/parakeet-cpu/ (encoder ~0.65 GB + decoder)

Why not the ready-made `encoder-model.int8.onnx`: it quantizes every convolution as well, and
with those 29 layers in int8 the encoder now and then returns nothing (or a few words) for a
whole window of speech - worst when the window contains very quiet or silent stretches.
Measured 2026-11-19 on real recordings (12 s windows of a monologue; a 13 min TV episode
scored against its subtitles):

    encoder                       windows with < 60 % of the words   word error   10 s clip
    ready-made int8               10.5 %                             18.3 %       241 ms
    this one                       0.9 %                             15.8 %       265 ms
    fp32 (reference)               -                                 15.2 %       341 ms

The decoder keeps its ready-made int8 version (fp32 encoder + int8 decoder: 0.9 %).
Needs the fp32 model (downloaded on demand, 2.4 GB) and the `onnx` package (uv sync --group bench).
"""

import shutil
import sys
import time
from pathlib import Path

import onnx
from onnxruntime.quantization import QuantType, quantize_dynamic

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from livecapture import models  # noqa: E402

SMALL = ["config.json", "vocab.txt", "nemo128.onnx", "decoder_joint-model.int8.onnx"]


def main() -> None:
    src = models.ensure_parakeet(None) / "encoder-model.onnx"
    models._ensure(models.PARAKEET_REPO, models.PARAKEET_DIR, SMALL)  # noqa: SLF001
    out = models.PARAKEET_CPU_DIR
    out.mkdir(parents=True, exist_ok=True)
    graph = onnx.load(str(src), load_external_data=False).graph
    keep_fp32 = [n.name for n in graph.node if n.op_type == "Conv" and "pointwise" not in n.name]
    print(f"{len(keep_fp32)} convolutions stay fp32 (subsampling + depthwise)")
    t = time.time()
    dst = out / "encoder-model.int8.onnx"
    quantize_dynamic(str(src), str(dst), weight_type=QuantType.QUInt8,
                     op_types_to_quantize=["MatMul", "Conv"], nodes_to_exclude=keep_fp32)
    for f in SMALL:
        shutil.copy2(models.PARAKEET_DIR / f, out / f)
    print(f"{dst}: {dst.stat().st_size / 2**20:.0f} MiB in {time.time() - t:.0f} s")


if __name__ == "__main__":
    main()
