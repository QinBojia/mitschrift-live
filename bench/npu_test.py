"""Try Parakeet-TDT-0.6B-v3 encoder on Intel NPU (or iGPU) via OpenVINO.

The encoder (≈99% of compute) runs on OpenVINO with a static input window; the
preprocessor and the small TDT decoder stay on CPU via onnx-asr / ONNX Runtime.
Segments shorter than the window are zero-padded and masked by `length`.

Usage:
    python npu_test.py --device NPU --windows 10
    python npu_test.py --device GPU --windows 10      # Intel iGPU
"""

import argparse
import gc
import json
import time
from pathlib import Path

import numpy as np
import openvino as ov
import psutil
import soundfile as sf

import parakeet_bench as pb

FRAMES_PER_SEC = 100  # nemo128 preprocessor: 10 ms hop


class OvEncoder:
    """Drop-in for the ORT encoder session: run(["outputs","encoded_lengths"], feed)."""

    def __init__(self, core: ov.Core, onnx_path: Path, device: str, windows_s: list[float], cache_dir: Path):
        core.set_property({"CACHE_DIR": str(cache_dir)})
        base = core.read_model(str(onnx_path))
        self.buckets = []  # (frames, compiled request)
        self.compile_s = {}
        for w in sorted(windows_s):
            frames = int(w * FRAMES_PER_SEC) + 8
            m = base.clone()
            m.reshape({"audio_signal": [1, 128, frames], "length": [1]})
            t = time.perf_counter()
            cm = core.compile_model(m, device, {"PERFORMANCE_HINT": "LATENCY"})
            self.compile_s[w] = round(time.perf_counter() - t, 1)
            print(f"  compiled {w:>4}s window ({frames} frames) on {device} in {self.compile_s[w]}s")
            self.buckets.append((frames, cm.create_infer_request()))
        self.calls = 0

    def run(self, names, feed):
        feats, lens = feed["audio_signal"], feed["length"]
        t = feats.shape[2]
        for frames, req in self.buckets:
            if t <= frames:
                break
        else:
            raise ValueError(f"segment of {t} frames exceeds largest window {self.buckets[-1][0]}")
        padded = np.zeros((1, 128, frames), dtype=np.float32)
        padded[:, :, :t] = feats
        res = req.infer({"audio_signal": padded, "length": lens.astype(np.int64)})
        enc_lens = np.asarray(res["encoded_lengths"]).astype(np.int64)
        out = np.asarray(res["outputs"])
        self.calls += 1
        return [out[:, :, : int(enc_lens.max())], enc_lens]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="NPU")
    ap.add_argument("--windows", type=float, nargs="+", default=[10.0])
    ap.add_argument("--model-dir", default=str(pb.DEFAULT_MODEL_DIR))
    ap.add_argument("--encoder", default=None, help="encoder .onnx or OpenVINO .xml (default: fp32 onnx)")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--streams", type=int, default=2)
    ap.add_argument("--hop", type=float, default=0.5)
    ap.add_argument("--reps", type=int, default=8)
    args = ap.parse_args()

    core = ov.Core()
    print("OpenVINO", ov.get_version(), "| devices:", core.available_devices)
    for d in core.available_devices:
        try:
            print(f"  {d}: {core.get_property(d, 'FULL_DEVICE_NAME')}")
        except Exception:  # noqa: BLE001
            pass

    proc = psutil.Process()
    rss0 = proc.memory_info().rss
    # CPU side (preprocessor + small TDT decoder) from the int8 ORT model; its encoder is swapped out below
    model = pb.load(args.model_dir, "cpu", "int8", threads=2, spin=False)
    model_dir = Path(args.model_dir)
    encoder_path = Path(args.encoder) if args.encoder else model_dir / "encoder-model.onnx"
    tag = args.tag or f"ov-{args.device.lower()}"
    t = time.perf_counter()
    enc = OvEncoder(core, encoder_path, args.device, args.windows,
                    Path.home() / ".livecapture/ov_cache")
    load_s = time.perf_counter() - t
    model.asr._encoder = enc  # swap ORT CPU encoder for OpenVINO
    gc.collect()
    rss1 = proc.memory_info().rss
    print(f"encoder ready in {load_s:.1f}s | process RAM {rss1 / 2**20:.0f} MiB (+{(rss1 - rss0) / 2**20:.0f})")

    audio_dir = Path(__file__).parent / "audio"
    refs = json.loads((audio_dir / "refs.json").read_text(encoding="utf-8"))
    clips = {lang: sf.read(audio_dir / f"{lang}.wav", dtype="float32")[0] for lang in refs}
    long_audio = np.concatenate([clips["de"], clips["en"]])
    win = max(args.windows)

    model.recognize(clips["en"][: 2 * pb.SR])  # warm-up

    # accuracy: transcribe each clip in window-sized chunks
    acc = {}
    for lang, ref in refs.items():
        a = clips[lang]
        step = int(win * pb.SR)
        hyp = " ".join(model.recognize(a[i : i + step]) for i in range(0, len(a), step))
        acc[lang] = {"wer": round(pb.wer(ref, hyp), 4), "text": hyp}
        print(f"[{lang}] WER {acc[lang]['wer']:.1%} (chunked by {win:.0f}s)\n  {hyp}")

    print("latency by segment length:")
    lens = [x for x in [1, 2, 5, 10, 20, 30] if x <= win]
    lat = pb.latency_by_length(model, long_audio, lens, args.reps)

    print(f"real-time sim: {args.streams} streams, hop {args.hop}s, utterance up to {win}s, {args.duration:.0f}s ...")
    rt_res = pb.realtime_sim(model, long_audio, args.streams, args.hop, win, args.duration)
    print("  " + json.dumps(rt_res))

    result = {
        "tag": tag,
        "encoder": encoder_path.name,
        "openvino": ov.get_version(),
        "windows_s": args.windows,
        "compile_s": enc.compile_s,
        "encoder_load_s": round(load_s, 1),
        "rss_mib": round(rss1 / 2**20),
        "accuracy": acc,
        "latency": lat,
        "realtime": rt_res,
    }
    out = Path(__file__).parent / "results" / f"{tag}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", out.name)


if __name__ == "__main__":
    main()
