"""Build a speaker-echo scenario for testing echo handling.

other.wav : far end = German clip (what the speakers play / loopback reference)
me.wav    : microphone = far end through a simulated speaker+room (delay, reverb, gain)
            + the local user speaking English: overlapping the far end from 12 s
            (double-talk) and alone from 36 s
refs.json : what should appear under "me" (English only)
"""

import json
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 16_000
HERE = Path(__file__).parent


def load(p: Path) -> np.ndarray:
    return sf.read(p, dtype="float32")[0]


def room_ir(delay_s=0.10, gain=0.5, rt60=0.25, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int((delay_s + rt60) * SR)
    ir = np.zeros(n, dtype=np.float32)
    d = int(delay_s * SR)
    ir[d] = gain
    tail = rng.standard_normal(n - d - 1).astype(np.float32)
    decay = np.exp(-6.9 * np.arange(len(tail)) / (rt60 * SR))  # -60 dB over rt60
    ir[d + 1 :] = 0.15 * gain * tail * decay
    return ir


def place(dst: np.ndarray, src: np.ndarray, at_s: float, gain: float = 1.0) -> None:
    i = int(at_s * SR)
    dst[i : i + len(src)] += gain * src[: len(dst) - i]


def main() -> None:
    out = HERE / "audio_echo"
    out.mkdir(exist_ok=True)
    far = load(HERE / "audio" / "de.wav")
    near1 = load(HERE / "audio_calib" / "en_calib2.wav")  # double-talk, from 12 s
    near2 = load(HERE / "audio_calib" / "en_calib1.wav")  # alone, from 36 s
    total = int(54 * SR)

    other = np.zeros(total, dtype=np.float32)
    place(other, far, 0.5)

    me = np.convolve(other, room_ir())[:total].astype(np.float32)  # echo via speaker + room
    place(me, near1, 12.0, gain=0.8)
    place(me, near2, 36.0, gain=0.8)
    me += np.random.default_rng(1).standard_normal(total).astype(np.float32) * 0.002  # mic noise

    sf.write(out / "other.wav", other, SR, subtype="PCM_16")
    sf.write(out / "me.wav", np.clip(me, -1, 1), SR, subtype="PCM_16")
    calib = json.loads((HERE / "audio_calib" / "refs.json").read_text(encoding="utf-8"))
    refs = {"me": calib["en_calib2"] + " " + calib["en_calib1"],
            "other": json.loads((HERE / "audio" / "refs.json").read_text(encoding="utf-8"))["de"]}
    (out / "refs.json").write_text(json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")
    echo_db = 20 * np.log10(np.sqrt(np.mean(np.convolve(far, room_ir()) ** 2)) / np.sqrt(np.mean(near1**2 * 0.64)))
    print(f"wrote {out} | echo level vs. local speech: {echo_db:+.1f} dB")


if __name__ == "__main__":
    main()
