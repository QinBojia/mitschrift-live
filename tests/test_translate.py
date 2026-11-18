"""Translator flow with a fake backend (no API calls)."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture.segmenter import Caption  # noqa: E402
from livecapture.translate import (  # noqa: E402
    PENDING,
    TranslationUnavailable,
    Translator,
    complete_prefix,
    detect_de_en,
    system_prompt,
)


class FakeBackend:
    def __init__(self, delay=0.05, fail=None):
        self.calls, self.delay, self.fail = [], delay, fail
        self.lock = threading.Lock()

    def __call__(self, text, src, target, context):
        with self.lock:
            self.calls.append((text, src, target, len(context)))
        time.sleep(self.delay)
        if self.fail:
            raise self.fail
        return f"<{target}:{text}>"


def run(captions, target="zh", backend=None):
    out = []
    backend = backend or FakeBackend()
    tr = Translator(out.append, backend, target)
    for c in captions:
        tr.on_caption(c)
    tr.flush(5)
    return out, backend, tr


def cap(text, final, utt=1, lang=None, stream="other", tentative=""):
    return Caption(stream, utt, 0.0, 1.0, text, tentative, final, lang)


def test_system_prompt_scene():
    p = system_prompt("zh", "a German language class")
    assert "Setting: a German language class." in p and "Simplified Chinese" in p
    assert "Setting" not in system_prompt("en", "") and "Setting" not in system_prompt("en", None)


def test_detect_de_en():
    assert detect_de_en("Guten Morgen, wir wollen zuerst die Ergebnisse sehen.") == "de"
    assert detect_de_en("Thanks everyone for joining, the prototype is working.") == "en"
    assert detect_de_en("Fußball") == "de"


def test_complete_prefix():
    assert complete_prefix("Erster Satz. Zweiter Satz ohne Ende") == len("Erster Satz.")
    assert complete_prefix("你好。今天") == len("你好。")
    assert complete_prefix("kein Ende") == 0


def test_german_final_is_held_then_delivered_with_translation():
    out, backend, _ = run([cap("", False, tentative="Guten"), cap("Guten Morgen zusammen.", True)])
    assert [c.final for c in out] == [False, False, True]
    assert out[1].translation == PENDING and out[1].committed == "Guten Morgen zusammen."
    assert out[-1].translation == "<zh:Guten Morgen zusammen.>"
    assert backend.calls[0][1:3] == ("de", "zh")


def test_long_utterance_sentences_are_translated_as_they_are_committed():
    caps = [
        cap("Wir haben die Texte gelesen.", False, tentative="Und dann"),
        cap("Wir haben die Texte gelesen. Und dann diskutieren wir", False),
        cap("Wir haben die Texte gelesen. Und dann diskutieren wir über den Verein.", True),
    ]
    out, backend, _ = run(caps)
    assert [c[0] for c in backend.calls] == [
        "Wir haben die Texte gelesen.",
        "Und dann diskutieren wir über den Verein.",
    ]
    assert out[-1].translation == "<zh:Wir haben die Texte gelesen.> <zh:Und dann diskutieren wir über den Verein.>"


def test_no_translation_when_source_is_target():
    out, backend, _ = run([cap("我们先看一下第三课的生词。", True, lang="zh")], target="zh")
    assert backend.calls == [] and out[-1].final and out[-1].translation is None
    out, backend, _ = run([cap("Thanks everyone for joining.", True)], target="en")
    assert backend.calls == [] and out[-1].translation is None


def test_chinese_translated_into_english():
    out, backend, _ = run([cap("我们先看一下第三课的生词。", True, lang="zh")], target="en")
    assert backend.calls[0][1:3] == ("zh", "en") and out[-1].translation.startswith("<en:")


def test_context_grows_with_previous_lines():
    out, backend, _ = run([cap("Erster Satz hier.", True, utt=1), cap("Zweiter Satz hier.", True, utt=2)],
                          backend=FakeBackend(delay=0))
    assert [c[3] for c in backend.calls] == [0, 1]


def test_missing_credentials_disable_translation_but_keep_captions():
    out, backend, tr = run([cap("Guten Morgen zusammen.", True, utt=1), cap("Noch ein Satz hier.", True, utt=2)],
                           backend=FakeBackend(fail=TranslationUnavailable("no key")))
    finals = [c for c in out if c.final]
    assert len(finals) == 2 and all(c.translation is None for c in finals)
    assert tr.disabled and tr.error == "no key"


def test_api_error_marks_line_but_continues():
    out, _, tr = run([cap("Guten Morgen zusammen.", True)], backend=FakeBackend(fail=RuntimeError("boom")))
    assert out[-1].final and "失败" in out[-1].translation and not tr.disabled


def test_target_off_passes_captions_without_translation():
    out, backend, _ = run([cap("Guten Morgen zusammen.", True)], target="off")
    assert backend.calls == [] and out[-1].final and out[-1].translation is None


def test_german_target():
    caps = [cap("Guten Morgen zusammen.", True, utt=1),
            cap("Thanks everyone for joining.", True, utt=2),
            cap("我们先看一下第三课的生词。", True, utt=3, lang="zh")]
    out, backend, _ = run(caps, target="de")
    assert sorted((c[1], c[2]) for c in backend.calls) == [("en", "de"), ("zh", "de")]  # German not re-translated
    finals = {c.utt_id: c.translation for c in out if c.final}
    assert finals[1] is None and finals[2].startswith("<de:") and finals[3].startswith("<de:")
    assert "into German" in system_prompt("de", None)


def test_switching_target_live():
    out = []
    backend = FakeBackend(delay=0)
    tr = Translator(out.append, backend, "zh")
    tr.on_caption(cap("Erster Satz hier.", True, utt=1))
    tr.set_target("en")
    tr.on_caption(cap("Zweiter Satz hier.", True, utt=2))
    tr.set_target("off")
    tr.on_caption(cap("Dritter Satz hier.", True, utt=3))
    tr.flush(5)
    finals = {c.utt_id: c.translation for c in out if c.final}
    assert finals == {1: "<zh:Erster Satz hier.>", 2: "<en:Zweiter Satz hier.>", 3: None}


def test_echo_removal_passes_through():
    out, backend, _ = run([cap("", True)])
    assert len(out) == 1 and out[0].final and backend.calls == []
