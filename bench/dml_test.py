"""Parakeet-TDT-0.6B-v3 encoder on a DirectML GPU (AMD / Intel / NVIDIA, any DX12 adapter).

Needs a venv with onnxruntime-directml instead of onnxruntime(-gpu), e.g. ~/.venvs/lc-dml.
The preprocessor and the TDT decoder stay on CPU (int8 ORT); only the encoder moves.

Usage:
    python dml_test.py --encoder fp16 --device 1              # dynamic length
    python dml_test.py --encoder fp16 --device 1 --window 10  # static 10 s window (padded)
    python dml_test.py --encoder int8 --device 1
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as rt
import psutil
import soundfile as sf

import parakeet_bench as pb

rt.set_default_logger_severity(3)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from livecapture import dml  # noqa: E402

ENCODERS = {"fp16": "encoder-model.fp16.onnx", "int8": "encoder-model.int8.onnx", "fp32": "encoder-model.onnx"}


class DmlEncoder:
    """Drop-in for the ORT encoder session: run(["outputs","encoded_lengths"], feed)."""

    def __init__(self, path: Path, device_id: int, window_s: float, static: bool = False):
        so = rt.SessionOptions()
        so.enable_mem_pattern = False  # required by the DirectML EP
        so.execution_mode = rt.ExecutionMode.ORT_SEQUENTIAL
        so.add_free_dimension_override_by_name("audio_signal_dynamic_axes_1", 1)
        so.add_free_dimension_override_by_name("length_dynamic_axes_1", 1)
        self.frames = int(window_s * 100) + 8 if window_s else 0
        if self.frames and static:  # NB: gives wrong outputs on DirectML (ORT 1.24); padding alone is fine
            so.add_free_dimension_override_by_name("audio_signal_dynamic_axes_2", self.frames)
        self.sess = rt.InferenceSession(str(path), so, providers=[("DmlExecutionProvider", {"device_id": device_id})])
        self.calls = 0

    def run(self, _names, feed):
        feats = feed["audio_signal"]
        if self.frames:
            t = feats.shape[2]
            if t > self.frames:
                raise ValueError(f"{t} frames > window {self.frames}")
            padded = np.zeros((1, 128, self.frames), dtype=np.float32)
            padded[:, :, :t] = feats
            feats = padded
        out, lens = self.sess.run(["outputs", "encoded_lengths"], {"audio_signal": feats, "length": feed["length"]})
        self.calls += 1
        return [out[:, :, : int(lens.max())], lens]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--encoder", default="fp16", help="fp16 | int8 | fp32 | path to .onnx")
    ap.add_argument("--device", type=int, default=None, help="DXGI adapter index (default: integrated GPU)")
    ap.add_argument("--window", type=float, default=0.0, help="static window in seconds (0 = dynamic)")
    ap.add_argument("--static", action="store_true", help="also declare the window as a static dim")
    ap.add_argument("--model-dir", default=str(pb.DEFAULT_MODEL_DIR))
    ap.add_argument("--tag", default=None)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--reps", type=int, default=8)
    args = ap.parse_args()

    ads = dml.adapters()
    for a in ads:
        print(" ", a)
    dev = next(a for a in ads if a.index == args.device) if args.device is not None else dml.pick()
    print("ORT", rt.__version__, "| using", dev)

    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    model = pb.load(args.model_dir, "cpu", "int8", threads=2, spin=False)
    enc_path = Path(args.encoder) if args.encoder.endswith(".onnx") else Path(args.model_dir) / ENCODERS[args.encoder]
    t = time.perf_counter()
    enc = DmlEncoder(enc_path, dev.index, args.window, args.static)
    load_s = time.perf_counter() - t
    model.asr._encoder = enc
    rss1 = proc.memory_info().rss
    print(f"encoder {enc_path.name} ready in {load_s:.1f}s | RAM {rss1 / 2**20:.0f} MiB (+{(rss1 - rss0) / 2**20:.0f})")

    audio_dir = Path(__file__).parent / "audio"
    refs = json.loads((audio_dir / "refs.json").read_text(encoding="utf-8"))
    clips = {lang: sf.read(audio_dir / f"{lang}.wav", dtype="float32")[0] for lang in refs}
    long_audio = np.concatenate([clips["de"], clips["en"]])
    win = args.window or 10.0
    t = time.perf_counter()
    model.recognize(clips["en"][: 2 * pb.SR])  # warm-up (first call compiles shaders)
    print(f"first call {1000 * (time.perf_counter() - t):.0f} ms")

    acc = {}
    for lang, ref in refs.items():
        a, step = clips[lang], int(win * pb.SR)
        hyp = " ".join(model.recognize(a[i : i + step]) for i in range(0, len(a), step))
        acc[lang] = {"wer": round(pb.wer(ref, hyp), 4), "text": hyp}
        print(f"[{lang}] WER {acc[lang]['wer']:.1%}")

    print("latency by segment length:")
    lat = pb.latency_by_length(model, long_audio, [x for x in [1, 2, 5, 10] if x <= win], args.reps)
    print(f"real-time sim: 2 streams, hop 0.5s, utterance up to {win:.0f}s, {args.duration:.0f}s ...")
    rt_res = pb.realtime_sim(model, long_audio, 2, 0.5, win, args.duration)
    print("  " + json.dumps(rt_res))

    tag = args.tag or f"dml-{args.encoder}-{('s%d' if args.static else 'w%d') % args.window if args.window else 'dyn'}-{dev.vendor.lower()}"
    out = Path(__file__).parent / "results" / f"{tag}.json"
    out.write_text(json.dumps({"tag": tag, "device": dev.name, "encoder": enc_path.name, "ort": rt.__version__,
                               "window_s": args.window, "load_s": round(load_s, 1), "rss_mib": round(rss1 / 2**20),
                               "accuracy": acc, "latency": lat, "realtime": rt_res},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", out.name)


if __name__ == "__main__":
    main()
