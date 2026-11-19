"""Captions follow sentences: grouping (sentence_groups), live cuts at sentence ends while the
speech goes on, and the split of what is left when the speaker pauses."""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_gaps import Scripted  # noqa: E402

from livecapture.segmenter import SR, SegmenterConfig, StreamProcessor, capitalized, count_words, sentence_groups  # noqa: E402
from livecapture.stt import Word, words_text  # noqa: E402


def words(text: str) -> list[Word]:
    return [Word(w, i * 0.4, i * 0.4 + 0.2) for i, w in enumerate(text.split())]


def groups(text: str, min_words: int = 3, have: int = 0) -> tuple[list[str], str]:
    done, carry = sentence_groups(words(text), min_words, have)
    return [words_text(g) for g in done], words_text(carry)


def test_one_caption_per_sentence():
    assert groups("We must leave now, Martin. This is the last train. The station closes in an hour. And what") == (
        ["We must leave now, Martin.", "This is the last train.", "The station closes in an hour."], "And what")


def test_short_sentences_wait_for_the_next_one():
    assert groups("No. It is rather cold outside, I'm afraid. Yes. Thank you. Come") == (
        ["No. It is rather cold outside, I'm afraid.", "Yes. Thank you."], "Come")
    assert groups("Yes.") == ([], "Yes.")
    assert groups("Yes.", have=2) == (["Yes."], "")  # the caption already holds two words
    assert groups("Dr. Weber kommt am 3. Mai. Gut.") == (["Dr. Weber kommt am 3. Mai."], "Gut.")


def test_count_words_and_capitals():
    assert count_words("Guten Morgen zusammen") == 3
    assert count_words("我们先看") == 4
    assert count_words("") == 0
    assert capitalized("the train is late.") == "The train is late."
    assert capitalized("") == ""


def sentences_script(seconds: float) -> list[tuple[str, float]]:
    """Speech without pauses: a word every 0.45 s, sentences of 5, 1, 6, 2, 8 and 4 words."""
    out, t, i = [], 0.5, 0
    lengths = [5, 1, 6, 2, 8, 4]
    while t < seconds - 1.0:
        n = lengths[len([w for w, _ in out if w.endswith(".")]) % len(lengths)]
        for j in range(n):
            if t >= seconds - 1.0:
                break
            out.append((f"w{i}" + ("." if j == n - 1 else ""), round(t, 2)))
            t += 0.45
            i += 1
    return out


def run(script: list[tuple[str, float]], seconds: float, live: bool, speech_until: float | None = None):
    """live: feed in step with the processing, so partial results (and sentence cuts) happen
    as in real time; otherwise everything at once, which skips the partials."""
    engine = Scripted(script)
    finals: list[str] = []
    limit = (speech_until or seconds + 1) * SR * Scripted.STEP

    def vad(frame):
        return 0.9 if frame[0] < limit else 0.0

    proc = StreamProcessor("other", engine, vad, SegmenterConfig(max_segment=9.0, adaptive_factor=0.0),
                           lambda c: finals.append(c.committed) if c.final and c.committed else None, live=False)
    audio = (np.arange(int(seconds * SR)) * Scripted.STEP).astype(np.float32)
    proc.start()
    for i in range(0, len(audio), 1600):
        proc.feed(audio[i : i + 1600])
        while live and proc._q.qsize() > 0:  # noqa: SLF001
            time.sleep(0.0005)
    proc.stop()
    proc.join()
    return finals, proc


def expected(script: list[tuple[str, float]]) -> list[str]:
    done, carry = sentence_groups([Word(w, t, t + 0.2) for w, t in script], 3)
    return [words_text(g) for g in done] + ([words_text(carry)] if carry else [])


def test_running_speech_becomes_one_caption_per_sentence():
    s = sentences_script(40.0)
    for live in (True, False):
        finals, proc = run(s, 40.0, live)
        assert [f.lower() for f in finals] == expected(s), live
        assert all(f[0] == "W" for f in finals[1:])  # each follows a sentence end
        if live:
            assert proc.sentence_cuts >= 5 and proc.forced_cuts == 0  # closed while the speech went on
        else:
            assert proc.forced_cuts >= 3  # the same captions, this time from forced cuts


def test_a_pause_after_several_sentences_gives_several_captions():
    s = sentences_script(8.0)  # 5 + 1 + 6 words, then two more
    finals, proc = run(s, 12.0, live=False, speech_until=8.0)
    assert [f.lower() for f in finals] == expected(s)
    assert len(finals) == 3 and proc.forced_cuts == 0 and proc.sentence_cuts == 0
