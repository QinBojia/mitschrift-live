"""Resource benchmark for NVIDIA Parakeet-TDT-0.6B-v3 (ONNX, via onnx-asr).

Measures: load time, RAM / VRAM footprint, accuracy on de/en samples, latency per
segment length, and a simulated real-time workload (N streams re-transcribing a
growing utterance buffer every `hop` seconds, like live partial captions).

Usage:
    python parakeet_bench.py --provider cuda --quant fp32
    python parakeet_bench.py --provider cpu  --quant int8
"""

import argparse
import json
import re
import statistics
import threading
import time
from pathlib import Path

import numpy as np
import onnxruntime as rt
import psutil
import soundfile as sf

try:
    rt.preload_dlls()  # load CUDA / cuDNN DLLs shipped as nvidia-* pip packages
except Exception as e:  # noqa: BLE001
    print("preload_dlls skipped:", e)

import onnx_asr  # noqa: E402

try:
    import pynvml

    pynvml.nvmlInit()
    _GPU = pynvml.nvmlDeviceGetHandleByIndex(0)
except Exception:  # noqa: BLE001
    pynvml = None

SR = 16_000
MODEL = "nemo-parakeet-tdt-0.6b-v3"
DEFAULT_MODEL_DIR = Path.home() / ".livecapture/models/parakeet-tdt-0.6b-v3-onnx"


def gpu_stats() -> tuple[float, float, float] | None:
    """(VRAM used MiB, util %, power W) for the whole GPU."""
    if pynvml is None:
        return None
    mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU).used / 2**20
    util = pynvml.nvmlDeviceGetUtilizationRates(_GPU).gpu
    power = pynvml.nvmlDeviceGetPowerUsage(_GPU) / 1000
    return mem, util, power


class GpuSampler(threading.Thread):
    def __init__(self, interval: float = 0.2):
        super().__init__(daemon=True)
        self.interval = interval
        self.samples: list[tuple[float, float, float]] = []
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            s = gpu_stats()
            if s:
                self.samples.append(s)
            time.sleep(self.interval)

    def stop(self) -> dict:
        self._halt.set()
        self.join()
        if not self.samples:
            return {}
        mem, util, power = zip(*self.samples)
        return {
            "vram_max_mib": round(max(mem)),
            "gpu_util_mean": round(statistics.mean(util), 1),
            "gpu_util_max": max(util),
            "power_mean_w": round(statistics.mean(power), 1),
        }


def normalize(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower()).split()


def wer(ref: str, hyp: str) -> float:
    r, h = normalize(ref), normalize(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(len(r), 1)


def load(model_dir: str, provider: str, quant: str, threads: int | None, spin: bool):
    so = rt.SessionOptions()
    if threads:
        so.intra_op_num_threads = threads
    if not spin:
        # ORT worker threads busy-wait by default; that burns whole cores in a mostly-idle real-time app
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        so.add_session_config_entry("session.inter_op.allow_spinning", "0")
    if provider == "cuda":
        providers = [("CUDAExecutionProvider", {"cudnn_conv_algo_search": "HEURISTIC"}), "CPUExecutionProvider"]
    else:
        providers = ["CPUExecutionProvider"]
    return onnx_asr.load_model(
        MODEL, model_dir, quantization=None if quant == "fp32" else quant, providers=providers, sess_options=so
    )


def latency_by_length(model, audio: np.ndarray, lengths: list[float], reps: int) -> list[dict]:
    rows = []
    rng = np.random.default_rng(0)
    for L in lengths:
        n = int(L * SR)
        starts = rng.integers(0, len(audio) - n, size=reps + 1)
        times = []
        for s in starts:
            a = time.perf_counter()
            model.recognize(audio[s : s + n])
            times.append(time.perf_counter() - a)
        warm = times[1:]
        med = statistics.median(warm)
        rows.append(
            {
                "seconds": L,
                "first_ms": round(times[0] * 1000),
                "median_ms": round(med * 1000),
                "p90_ms": round(sorted(warm)[int(0.9 * (len(warm) - 1))] * 1000),
                "rtf": round(med / L, 4),
            }
        )
        print(f"  {L:5.1f}s  first {rows[-1]['first_ms']:5d} ms  median {rows[-1]['median_ms']:5d} ms  "
              f"p90 {rows[-1]['p90_ms']:5d} ms  RTF {rows[-1]['rtf']:.4f}")
    return rows


def realtime_sim(model, audio: np.ndarray, streams: int, hop: float, max_utt: float, duration: float) -> dict:
    """Every `hop` s, each stream re-transcribes its current utterance (grows to max_utt, then resets)."""
    proc = psutil.Process()
    proc.cpu_percent(None)
    sampler = GpuSampler()
    sampler.start()
    utt = [(i * max_utt / streams) for i in range(streams)]
    lat: list[float] = []
    busy = 0.0
    late_ticks = 0
    t0 = next_t = time.perf_counter()
    tick = 0
    while time.perf_counter() - t0 < duration:
        for s in range(streams):
            utt[s] = utt[s] + hop if utt[s] + hop <= max_utt else hop
            n = int(utt[s] * SR)
            start = int((tick * hop + s * 11.0) * SR) % (len(audio) - n)
            a = time.perf_counter()
            model.recognize(audio[start : start + n])
            d = time.perf_counter() - a
            lat.append(d)
            busy += d
        tick += 1
        next_t += hop
        wait = next_t - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        else:
            late_ticks += 1
    wall = time.perf_counter() - t0
    cpu_total = proc.cpu_percent(None)
    gpu = sampler.stop()
    lat_sorted = sorted(lat)
    return {
        "streams": streams,
        "hop_s": hop,
        "max_utt_s": max_utt,
        "calls": len(lat),
        "duty_cycle": round(busy / wall, 3),
        "late_ticks": late_ticks,
        "ticks": tick,
        "lat_median_ms": round(statistics.median(lat) * 1000),
        "lat_p95_ms": round(lat_sorted[int(0.95 * (len(lat) - 1))] * 1000),
        "proc_cpu_pct_of_machine": round(cpu_total / psutil.cpu_count(), 1),
        "rss_mib": round(proc.memory_info().rss / 2**20),
        **gpu,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["cuda", "cpu"], default="cuda")
    ap.add_argument("--quant", choices=["fp32", "int8"], default="fp32")
    ap.add_argument("--model-dir", default=str(DEFAULT_MODEL_DIR))
    ap.add_argument("--spin", action="store_true", help="allow ORT thread spinning (default off)")
    ap.add_argument("--threads", type=int, default=None, help="CPU intra-op threads")
    ap.add_argument("--audio", default=str(Path(__file__).parent / "audio"))
    ap.add_argument("--streams", type=int, default=2)
    ap.add_argument("--hop", type=float, default=0.5)
    ap.add_argument("--max-utt", type=float, default=10.0)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--reps", type=int, default=8)
    args = ap.parse_args()
    tag = f"{args.provider}-{args.quant}" + (f"-t{args.threads}" if args.threads else "") + ("-spin" if args.spin else "")
    print(f"=== {tag} | onnxruntime {rt.__version__} | available: {rt.get_available_providers()}")

    audio_dir = Path(args.audio)
    refs = json.loads((audio_dir / "refs.json").read_text(encoding="utf-8"))
    clips = {lang: sf.read(audio_dir / f"{lang}.wav", dtype="float32")[0] for lang in refs}
    long_audio = np.concatenate([clips["de"], clips["en"]])

    proc = psutil.Process()
    idle = GpuSampler()
    idle.start()
    time.sleep(3)
    gpu_idle = idle.stop()
    rss0 = proc.memory_info().rss
    vram0 = (gpu_stats() or (0, 0, 0))[0]

    t = time.perf_counter()
    model = load(args.model_dir, args.provider, args.quant, args.threads, args.spin)
    load_s = time.perf_counter() - t
    rss1 = proc.memory_info().rss
    vram1 = (gpu_stats() or (0, 0, 0))[0]
    print(f"load {load_s:.1f}s | RAM +{(rss1 - rss0) / 2**20:.0f} MiB | VRAM +{vram1 - vram0:.0f} MiB "
          f"(idle GPU util {gpu_idle.get('gpu_util_mean')}%)")

    t = time.perf_counter()
    model.recognize(clips["en"][: 2 * SR])
    warmup_ms = (time.perf_counter() - t) * 1000

    acc = {}
    for lang, ref in refs.items():
        t = time.perf_counter()
        hyp = model.recognize(clips[lang])
        dt = time.perf_counter() - t
        acc[lang] = {"wer": round(wer(ref, hyp), 4), "seconds": round(len(clips[lang]) / SR, 1),
                     "ms": round(dt * 1000), "text": hyp}
        print(f"[{lang}] WER {acc[lang]['wer']:.1%} | {acc[lang]['seconds']}s audio in {acc[lang]['ms']} ms\n  {hyp}")

    print("latency by segment length:")
    lat = latency_by_length(model, long_audio, [1, 2, 5, 10, 20, 30], args.reps)

    print(f"real-time sim: {args.streams} streams, update every {args.hop}s, utterance up to {args.max_utt}s, "
          f"{args.duration:.0f}s ...")
    rt_res = realtime_sim(model, long_audio, args.streams, args.hop, args.max_utt, args.duration)
    print("  " + json.dumps(rt_res))

    vram_after = (gpu_stats() or (0, 0, 0))[0]
    result = {
        "tag": tag,
        "onnxruntime": rt.__version__,
        "load_s": round(load_s, 1),
        "warmup_ms": round(warmup_ms),
        "ram_model_mib": round((rss1 - rss0) / 2**20),
        "rss_end_mib": round(proc.memory_info().rss / 2**20),
        "vram_model_mib": round(vram1 - vram0),
        "vram_end_delta_mib": round(vram_after - vram0),
        "gpu_idle": gpu_idle,
        "accuracy": acc,
        "latency": lat,
        "realtime": rt_res,
    }
    out = Path(__file__).parent / "results"
    out.mkdir(exist_ok=True)
    (out / f"{tag}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved results/{tag}.json")


if __name__ == "__main__":
    main()
