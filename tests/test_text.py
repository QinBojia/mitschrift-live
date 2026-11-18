"""CJK-aware text handling and SenseVoice token grouping."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture.echo import _words, containment  # noqa: E402
from livecapture.sensevoice import SenseVoice  # noqa: E402
from livecapture.stt import Word, join_text, words_text  # noqa: E402


def group(tokens):
    words: list[Word] = []
    for i, t in enumerate(tokens):
        SenseVoice._add_token(words, t, i * 0.06)  # noqa: SLF001
    return words


def test_join_text_no_space_for_chinese():
    assert join_text("我们先看", "第三课。") == "我们先看第三课。"
    assert join_text("Guten Morgen,", "danke.") == "Guten Morgen, danke."
    assert join_text("这个词", "Ehrenamt") == "这个词Ehrenamt"
    assert join_text("", "x") == "x"


def test_sensevoice_tokens_grouped_into_words():
    ws = group(["<|zh|>", "开", "放", "▁Ehren", "amt", "这", "个", "，", "词", "。"])
    assert [w.text for w in ws] == ["开", "放", "Ehrenamt", "这", "个，", "词。"]
    assert words_text(ws) == "开放 Ehrenamt这个，词。"
    assert [w.space for w in ws] == [False, False, True, False, False, False]


def test_latin_token_right_after_chinese_starts_a_new_word():
    ws = group(["生", "词", "。", "air", "y", "▁named", "这"])
    assert [w.text for w in ws] == ["生", "词。", "airy", "named", "这"]


def test_echo_tokens_split_chinese_characters():
    assert _words("你好，Welt 2026!") == ["你", "好", "welt", "2026"]
    assert containment(_words("我们看第三课"), _words("好，我们先看一下第三课的生词")) == 1.0
