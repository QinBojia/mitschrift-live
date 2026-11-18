"""Alternating German / Chinese / English audio through the full pipeline.

Checks per segment that utterances were routed to the right recognizer (lang tag) and
scores the text: WER for de/en, CER for zh.

Usage: python mixed_eval.py [cuda|npu|cpu]
"""

import json
import re
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from livecapture import models  # noqa: E402
from livecapture.audio import FileSource  # noqa: E402
from livecapture.segmenter import SegmenterConfig, StreamProcessor  # noqa: E402
from livecapture.sensevoice import SenseVoice  # noqa: E402
from livecapture.stt import SttEngine  # noqa: E402
from livecapture.vad import SileroVad  # noqa: E402
from parakeet_bench import wer  # noqa: E402

SR = 16_000
HERE = Path(__file__).parent
PLAN = [("de", "audio", "de"), ("zh", "audio_zh", "zh1"), ("en", "audio", "en"), ("zh", "audio_zh", "zh2"),
        ("de", "audio_calib", "de_calib2"), ("zh", "audio_zh", "zh3"), ("en", "audio_calib", "en_calib1")]


def cer(ref: str, hyp: str) -> float:
    r, h = (list(re.sub(r"[\W_]", "", s.lower())) for s in (ref, hyp))
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(len(r), 1)


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else "cuda"
    gap = np.zeros(int(1.2 * SR), dtype=np.float32)
    parts, segs, t = [], [], 0.0
    for lang, folder, name in PLAN:
        a = sf.read(HERE / folder / f"{name}.wav", dtype="float32")[0]
        ref = json.loads((HERE / folder / "refs.json").read_text(encoding="utf-8"))[name]
        parts += [a, gap]
        segs.append((lang, name, t, t + len(a) / SR, ref))
        t += (len(a) + len(gap)) / SR
    mix = HERE / "results" / "mixed.wav"
    sf.write(mix, np.concatenate(parts), SR, subtype="PCM_16")

    engine = SttEngine(kind)
    finals = []
    proc = StreamProcessor("other", engine, SileroVad(models.ensure_silero()),
                           SegmenterConfig(max_segment=min(12.0, engine.max_seconds - 1.0)),
                           lambda c: c.final and c.committed and finals.append(c), live=False,
                           sensevoice=SenseVoice(models.ensure_sensevoice()))
    src = FileSource(mix, speed=3.0)
    proc.start()
    src.start(proc.feed)
    src.done.wait()
    proc.stop()
    proc.join()

    t0 = proc._t0_epoch  # noqa: SLF001
    wrong = 0
    print(f"engine {kind} | {len(finals)} utterances, {proc.zh_utterances} routed to SenseVoice")
    for lang, name, s, e, ref in segs:
        mine = [c for c in finals if s - 0.5 <= (c.start + c.end) / 2 - t0 <= e + 0.5]
        bad = [c for c in mine if (c.lang == "zh") != (lang == "zh")]
        wrong += len(bad)
        hyp = ("" if lang == "zh" else " ").join(c.committed for c in mine)
        score = f"CER {cer(ref, hyp):5.1%}" if lang == "zh" else f"WER {wer(ref, hyp):5.1%}"
        print(f"\n[{lang} {name}] {len(mine)} utt, misrouted {len(bad)} | {score}")
        for c in mine:
            print(f"   {'zh' if c.lang == 'zh' else 'eu'} | {c.committed}")
    print(f"\nmisrouted utterances: {wrong}")


if __name__ == "__main__":
    main()
