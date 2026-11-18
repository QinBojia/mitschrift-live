"""drop_overlap cases taken from real forced-cut traces (bench/replay_eval.py, de.wav)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture.segmenter import choose_boundary, drop_overlap  # noqa: E402
from livecapture.stt import Word, words_from_tokens  # noqa: E402


def W(*pairs: tuple[str, float]) -> list[Word]:
    return [Word(t, s, s) for t, s in pairs]


def texts(words: list[Word]) -> list[str]:
    return [w.text for w in words]


def test_repeated_words_are_dropped():
    words = W(("Kurz", 0.16), ("auf", 0.4), ("die", 0.48), ("Ergebnisse", 0.56))
    assert texts(drop_overlap(words, ["zuerst", "kurz", "auf"], 0.30)) == ["die", "Ergebnisse"]


def test_junk_before_repeat_is_dropped():
    words = W(("Ich", 0.0), ("muss", 0.08), ("kurz", 0.16), ("auf", 0.4), ("die", 0.48))
    assert texts(drop_overlap(words, ["zuerst", "kurz", "auf"], 0.30)) == ["die"]


def test_clipped_fragment_is_dropped():
    words = W(("gleich", 0.16), ("zum", 0.4), ("Vorjahr", 0.56))
    assert texts(drop_overlap(words, ["ist", "im", "vergleich"], 0.30)) == ["zum", "Vorjahr"]
    words = W(("Dings", 0.08), ("sind", 0.4), ("auch", 0.56))
    assert texts(drop_overlap(words, ["12", "gestiegen", "allerdings"], 0.30)) == ["sind", "auch"]


def test_clean_start_is_kept():
    words = W(("schauen", 0.32), ("und", 0.72), ("danach", 0.88))
    assert texts(drop_overlap(words, ["des", "letzten", "quartals"], 0.30)) == ["schauen", "und", "danach"]


def test_unmatched_early_fragment_dropped_by_time():
    words = W(("äh", 0.0), ("bevor", 0.32), ("wir", 0.64))
    assert texts(drop_overlap(words, ["im", "detail", "vorstellt"], 0.30)) == ["bevor", "wir"]


def test_rehead_committed_word_is_dropped_but_next_word_kept():
    # the next word's timestamp can drift well before the expected boundary
    words = W(("joining.", 0.16), ("Before", 0.56), ("we", 1.0))
    assert texts(drop_overlap(words, ["everyone", "for", "joining"], 0.85)) == ["Before", "we"]


def test_no_tail_means_no_change():
    words = W(("Hallo", 0.0), ("zusammen", 0.3))
    assert drop_overlap(words, [], 0.0) == words


def test_words_from_tokens_groups_subwords_and_punctuation():
    ws = words_from_tokens([" G", "uten", " Mor", "gen", ",", " dan", "ke"], [0.0, 0.24, 0.48, 0.72, 0.8, 1.6, 1.76])
    assert texts(ws) == ["Guten", "Morgen,", "danke"]
    assert (ws[1].start, ws[1].last) == (0.48, 0.8)


def test_boundary_prefers_gap_after_punctuation():
    words = W(("a", 0.0), ("b,", 0.5), ("c", 1.2), ("d", 1.4), ("e", 1.6))
    assert choose_boundary(words, limit=2.0, min_prev_start=0.3) == 2  # cut before "c"


def test_boundary_respects_min_prev_start():
    words = W(("a", 0.0), ("b", 0.2), ("c", 0.4))
    assert choose_boundary(words, limit=2.0, min_prev_start=1.0) is None
