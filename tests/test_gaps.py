"""Speech the model left out: detection (unheard_speech), taking the words over from the
previous window (restore_heard), and the whole forced-cut machinery on a scripted recognizer."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture.segmenter import SR, SegmenterConfig, StreamProcessor, restore_heard, unheard_speech  # noqa: E402
from livecapture.stt import Transcript, Word, words_text  # noqa: E402
from livecapture.vad import FRAME  # noqa: E402

FPS = SR / FRAME  # VAD frames per second


def timed(*items: tuple[str, float]) -> list[Word]:
    return [Word(text, start, start + 0.2) for text, start in items]


def test_unheard_speech_finds_the_hole():
    speech = [True] * int(9 * FPS)
    dense = timed(*[(f"w{i}", 0.3 + i * 0.45) for i in range(19)])
    assert unheard_speech(dense, speech, 9.0) < 0.5
    # the trace of 2026-11-19: "... course." at 1.4 s, then "Because" at 4.1 s
    holed = timed(("Yes", 1.1), ("of", 1.3), ("course.", 1.4), ("Because", 4.1), ("we", 4.3), ("really", 4.5),
                  ("want", 5.7), ("to", 5.9), ("read", 6.6), ("it.", 8.4))
    assert unheard_speech(holed, speech, 9.0) > 1.5
    # the same hole without speech in it (a pause) is nothing to worry about
    pause = [not 1.6 < i / FPS < 4.0 for i in range(len(speech))]
    assert unheard_speech(holed, pause, 9.0) < 1.5
    assert unheard_speech([], speech, 9.0) > 8.0  # nothing recognized at all
    assert unheard_speech(dense[:8], speech, 9.0) > 4.0  # stopped early


def test_restore_heard_fills_only_the_hole():
    speech = [True] * int(9 * FPS)
    now = timed(("yes.", 1.0), ("Yes", 1.1), ("of", 1.3), ("course.", 1.4), ("Because", 4.1), ("we", 4.3),
                ("really", 4.5), ("want", 5.7), ("to", 5.9), ("read", 6.6), ("it.", 8.4))
    heard = timed(("Yes,", 1.13), ("of", 1.23), ("course.", 1.33), ("The", 1.63), ("library", 1.93), ("is", 2.13),
                  ("more", 2.33), ("or", 2.43), ("less", 2.53), ("free", 2.93), ("to", 3.23), ("use.", 3.43))
    text = words_text(restore_heard(now, heard, speech, 9.0, 1.5))
    assert text == ("yes. Yes of course. The library is more or less free to use. "
                    "Because we really want to read it.")
    assert restore_heard(now, [], speech, 9.0, 1.5) == now
    quiet = [False] * len(speech)  # no speech in the hole: nothing is put back
    assert restore_heard(now, heard, quiet, 9.0, 1.5) == now


class Scripted:
    """Recognizer for audio whose samples encode their own position (a ramp): returns the
    scripted words that fall inside the window, except those in `skip` (absolute seconds)
    for windows starting after `skip_from`."""

    STEP = 1e-6
    max_seconds = 10.0

    def __init__(self, script: list[tuple[str, float]], skip: tuple[float, float] | None = None, skip_from: float = 0.0):
        self.script, self.skip, self.skip_from = script, skip, skip_from
        self.windows: list[float] = []

    def stats(self):
        return None

    def transcribe(self, audio: np.ndarray) -> Transcript:
        if len(audio) < 2 or abs(float(audio[1] - audio[0]) - self.STEP) > self.STEP / 4:
            return Transcript("", [])  # not the plain ramp (the noisy retry view): nothing
        t0 = round(float(audio[0]) / self.STEP) / SR
        self.windows.append(t0)
        end = t0 + len(audio) / SR - 0.25
        words = [Word(text, t - t0, t - t0 + 0.2) for text, t in self.script
                 if t0 <= t <= end and not (self.skip and t0 > self.skip_from and self.skip[0] <= t <= self.skip[1])]
        return Transcript(words_text(words), words)


def run_scripted(engine: Scripted, seconds: float) -> list[str]:
    finals: list[str] = []
    proc = StreamProcessor("other", engine, lambda frame: 0.9, SegmenterConfig(max_segment=9.0),
                           lambda c: finals.append(c.committed) if c.final and c.committed else None, live=False)
    audio = (np.arange(int(seconds * SR)) * Scripted.STEP).astype(np.float32)
    proc.start()
    for i in range(0, len(audio), 1600):
        proc.feed(audio[i : i + 1600])
    proc.stop()
    proc.join()
    assert proc.forced_cuts >= 3
    return " ".join(finals).split()


def script(seconds: float) -> list[tuple[str, float]]:
    """A word every 0.45 s, with one longer pause after 4.8 s (where the first cut will land)."""
    out, t, i = [], 0.5, 0
    while t < seconds - 1.0:
        out.append((f"w{i}", round(t, 2)))
        t += 1.25 if 4.5 < t < 4.95 else 0.45
        i += 1
    return out


def test_speech_without_pauses_keeps_every_word_once():
    s = script(40.0)
    assert run_scripted(Scripted(s), 40.0) == [w for w, _ in s]


def test_a_passage_skipped_by_the_next_window_is_taken_from_the_previous_one():
    s = script(40.0)
    engine = Scripted(s, skip=(5.9, 8.4), skip_from=3.0)  # 5 words, heard by the first window only
    got = run_scripted(engine, 40.0)
    assert got == [w for w, _ in s]


def test_a_short_hole_is_left_alone():
    s = script(40.0)
    engine = Scripted(s, skip=(5.9, 7.0), skip_from=3.0)  # 2 words, ~1 s: below the threshold
    got = run_scripted(engine, 40.0)
    assert [w for w, _ in s if w not in got] == ["w11", "w12"]
    assert got == [w for w, _ in s if w not in ("w11", "w12")]
