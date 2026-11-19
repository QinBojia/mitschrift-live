"""How well does SenseVoice's language tag separate Chinese from German/English?

Takes windows of 1-4 s (like the start of a live utterance) from labelled clips and
reports P(zh) = p(zh)+p(yue); then scans the real recordings window by window.
"""

import statistics
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from livecapture import models  # noqa: E402
from livecapture.sensevoice import SenseVoice  # noqa: E402

SR = 16_000
HERE = Path(__file__).parent
LABELLED = {
    "de": ["audio/de.wav", "audio_calib/de_calib1.wav", "audio_calib/de_calib2.wav"],
    "en": ["audio/en.wav", "audio_calib/en_calib1.wav", "audio_calib/en_calib2.wav"],
    "zh": ["audio_zh/zh1.wav", "audio_zh/zh2.wav", "audio_zh/zh3.wav"],
}


def p_zh(probs: dict) -> float:
    return probs.get("zh", 0) + probs.get("yue", 0)


def main() -> None:
    sv = SenseVoice(models.ensure_sensevoice())
    rng = np.random.default_rng(0)
    timings = {}
    print("window | " + " | ".join(f"{k}: P(zh) min/median/max" for k in LABELLED))
    for win in [1.0, 1.5, 2.5, 4.0]:
        cells = []
        for lang, files in LABELLED.items():
            ps = []
            for f in files:
                a = sf.read(HERE / f, dtype="float32")[0]
                for _ in range(8):
                    s = int(rng.integers(0, len(a) - int(win * SR)))
                    t = time.perf_counter()
                    _, probs = sv.recognize(a[s : s + int(win * SR)])
                    timings.setdefault(win, []).append((time.perf_counter() - t) * 1000)
                    ps.append(p_zh(probs))
            cells.append(f"{min(ps):.2f}/{statistics.median(ps):.2f}/{max(ps):.2f}")
        print(f"{win:4.1f}s  | " + " | ".join(cells))
    print("SenseVoice CPU ms per call:", {w: round(statistics.median(v)) for w, v in timings.items()})

    for rec in sorted((HERE / "recordings").glob("*/other.*")):  # .mp3, or .wav in older recordings
        a = sf.read(rec, dtype="float32")[0]
        print(f"\n== {rec.parent.name} ({len(a) / SR:.0f}s), 3 s windows with speech:")
        for s in range(0, len(a) - 3 * SR, 3 * SR):
            w = a[s : s + 3 * SR]
            if np.sqrt(np.mean(w**2)) < 0.005:
                continue
            tr, probs = sv.recognize(w)
            print(f"  {s / SR:5.0f}s P(zh)={p_zh(probs):.2f} top={tr.lang:8s} {tr.text[:60]}")


if __name__ == "__main__":
    main()
