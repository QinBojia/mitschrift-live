"""Replay the test clips through the full live pipeline (VAD -> segmenter -> STT) and score WER.

Usage:
    python replay_eval.py --engine cuda
    python replay_eval.py --engine npu --max-segment 4     # stress the forced-cut logic
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from livecapture import models  # noqa: E402
from livecapture.audio import FileSource  # noqa: E402
from livecapture.segmenter import SegmenterConfig, StreamProcessor  # noqa: E402
from livecapture.stt import SttEngine  # noqa: E402
from livecapture.vad import SileroVad  # noqa: E402
from parakeet_bench import wer  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="cuda")
    ap.add_argument("--max-segment", type=float)
    ap.add_argument("--min-silence", type=float, default=0.5)
    ap.add_argument("--speed", type=float, default=4.0)
    args = ap.parse_args()

    engine = SttEngine(args.engine)
    cfg = SegmenterConfig(min_silence=args.min_silence,
                          max_segment=args.max_segment or min(12.0, engine.max_seconds - 1.0))
    audio_dir = Path(__file__).parent / "audio"
    refs = json.loads((audio_dir / "refs.json").read_text(encoding="utf-8"))
    print(f"engine {engine.kind} ({engine.device}) | max_segment {cfg.max_segment}s | speed x{args.speed}")

    for lang, ref in refs.items():
        finals, updates = [], []

        def on_caption(c, finals=finals, updates=updates):
            updates.append(c)
            if c.final and c.committed:
                finals.append(c)

        src = FileSource(audio_dir / f"{lang}.wav", args.speed)
        proc = StreamProcessor("other", engine, SileroVad(models.ensure_silero()), cfg, on_caption, live=False)
        proc.start()
        src.start(proc.feed)
        src.done.wait()
        proc.stop()
        proc.join()
        hyp = " ".join(c.committed for c in finals)
        partials = sum(1 for u in updates if not u.final)
        print(f"\n[{lang}] WER {wer(ref, hyp):.1%} | {len(finals)} utterances, {proc.forced_cuts} forced cuts, {proc.retries} retries, "
              f"{partials} partial updates")
        for c in finals:
            print(f"  {c.end - c.start:5.1f}s | {c.committed}")
    stats = engine.stats()
    print(f"\nSTT calls: mean {stats[0]:.0f} ms, p95 {stats[1]:.0f} ms")


if __name__ == "__main__":
    main()
