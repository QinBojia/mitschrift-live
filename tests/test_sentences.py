"""Sentence ends used to cut captions (segmenter.ends_sentence / sentence_split)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture.segmenter import ends_sentence, sentence_split  # noqa: E402
from livecapture.stt import Word  # noqa: E402


def words(text: str) -> list[Word]:
    return [Word(w, i * 0.4, i * 0.4 + 0.2) for i, w in enumerate(text.split())]


def test_sentence_ends():
    for t in ["Is how intelligence is made.", "Kommst du mit?", "Ja!", "我们先看一下第三课的生词。",
              "你好吗？", 'He said "no."', "Und dann...", "That's it.)"]:
        assert ends_sentence(t), t


def test_not_sentence_ends():
    for t in ["", "and the combination of", "Guten Morgen,", "Das ist z.B.", "Wir treffen uns am 3.",
              "Ich habe Dr.", "e.g.", "Plan A.", "大家注意，"]:
        assert not ends_sentence(t), t


def test_split_at_last_sentence_end():
    w = words("Yeah. You know you need some infrastructure that will contain agents, right? It will ensure")
    k = sentence_split(w)
    assert " ".join(x.text for x in w[:k]).endswith("right?")
    assert w[k].text == "It"


def test_no_sentence_end_means_no_split_unless_clauses_allowed():
    w = words("and this is how the combination of OpenShell, BlueField and")
    assert sentence_split(w) is None
    k = sentence_split(w, clause=True)
    assert w[k - 1].text == "OpenShell,"


def test_split_ignores_abbreviations():
    w = words("Das gilt z.B. für Verben")
    assert sentence_split(w) is None
