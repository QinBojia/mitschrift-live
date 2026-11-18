"""Time each stage (preprocess / encoder on NPU / TDT decode on CPU) per encoder variant."""

import statistics
import time
from pathlib import Path

import numpy as np
import openvino as ov
import soundfile as sf

import parakeet_bench as pb
from npu_test import OvEncoder

OV_DIR = pb.DEFAULT_MODEL_DIR.parent / "parakeet-ov"
VARIANTS = {
    "fp16": pb.DEFAULT_MODEL_DIR / "encoder-model.onnx",
    "int8w": OV_DIR / "encoder-int8w.xml",
    "int8q": OV_DIR / "encoder-int8q.xml",
}


def timed(fn, reps=15):
    fn()
    ts = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        ts.append(time.perf_counter() - t)
    return statistics.median(ts) * 1000


def main() -> None:
    core = ov.Core()
    audio = sf.read(Path(__file__).parent / "audio" / "de.wav", dtype="float32")[0]
    for dec_quant in ["int8", "fp32"]:
        model = pb.load(str(pb.DEFAULT_MODEL_DIR), "cpu", dec_quant, threads=2, spin=False)
        asr = model.asr
        for name, path in VARIANTS.items():
            asr._encoder = OvEncoder(core, path, "NPU", [10.0], Path.home() / ".livecapture/ov_cache")
            for secs in [2, 10]:
                seg = audio[: secs * pb.SR]
                wav, wav_len = seg[None], np.array([len(seg)], dtype=np.int64)
                feats, flen = asr._preprocessor(wav, wav_len)
                enc_out, enc_len = asr._encode(feats, flen)
                t_pre = timed(lambda: asr._preprocessor(wav, wav_len))
                t_enc = timed(lambda: asr._encode(feats, flen))
                t_dec = timed(lambda: list(asr._decoding(enc_out, enc_len)))
                t_all = timed(lambda: model.recognize(seg))
                print(f"decoder {dec_quant:5s} | encoder {name:6s} | {secs:2d}s: pre {t_pre:5.1f}  "
                      f"enc(NPU) {t_enc:6.1f}  dec(CPU) {t_dec:6.1f}  total {t_all:6.1f} ms")


if __name__ == "__main__":
    main()
