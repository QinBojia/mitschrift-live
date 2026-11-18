"""EchoFilter / EchoCanceller behaviour."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture.echo import EchoCanceller, EchoFilter, containment  # noqa: E402
from livecapture.segmenter import Caption  # noqa: E402

T0 = 1_700_000_000.0


def cap(stream, utt, start, end, text, final=True, tentative=""):
    return Caption(stream, utt, T0 + start, T0 + end, text, tentative, final)


def run(events):
    out = []
    f = EchoFilter(out.append)
    for e in events:
        f.on_caption(e)
    return f, out


def test_garbled_echo_overlapping_far_end_is_hidden():
    # from a real run: speakers + laptop mic, no headphones
    other = cap("other", 1, 0.0, 4.0, "Lesen Sie die zwei Texte über Ehrenamt im Fußballverein und auch über FC.")
    me = cap("me", 1, 0.1, 4.2, "Das sind sie die zwei Texte über Fammt im Fußball verheim und da auch über FC")
    f, out = run([other, me])
    assert out[-1].stream == "me" and out[-1].final and out[-1].committed == ""
    assert f.suppressed == 1


def test_reply_after_far_end_is_kept_even_if_words_repeat():
    other = cap("other", 1, 0.0, 4.0, "Lesen Sie die zwei Texte über Ehrenamt im Fußballverein.")
    me = cap("me", 1, 5.0, 7.0, "Ja, die zwei Texte über Ehrenamt habe ich gelesen.")
    f, out = run([other, me])
    assert out[-1].committed == me.committed and f.suppressed == 0


def test_own_speech_during_far_end_is_kept():
    other = cap("other", 1, 0.0, 6.0, "Der Umsatz ist im Vergleich zum Vorjahr gestiegen.")
    me = cap("me", 1, 1.0, 4.0, "Sorry, can I ask a quick question about the budget?")
    f, out = run([other, me])
    assert out[-1].committed == me.committed and f.suppressed == 0


def test_hidden_partial_reappears_if_final_is_not_echo():
    other = cap("other", 1, 0.0, 3.0, "Gibt es dazu Fragen oder Einwände?")
    p = cap("me", 2, 0.2, 2.5, "", final=False, tentative="gibt es dazu Fragen")
    final = cap("me", 2, 0.2, 5.0, "Gibt es eine Frage? Ja, ich habe eine Frage zum Termin am Donnerstag.")
    f, out = run([other, p, p, final])
    me_out = [c for c in out if c.stream == "me"]
    assert len(me_out) == 2  # one removal for the partial, the repeated partial is swallowed
    assert me_out[-1].committed == final.committed


def test_containment_is_fuzzy():
    assert containment(["fußball", "verheim"], ["fußballverein"]) == 0.5
    assert containment([], ["a"]) == 0.0


def test_echo_canceller_removes_pure_echo():
    sr = 16_000
    rng = np.random.default_rng(0)
    far = (rng.standard_normal(sr * 6) * 0.1).astype(np.float32)  # 6 s of far-end noise-like signal
    echo = np.concatenate([np.zeros(800, np.float32), far[:-800]]) * 0.5  # 50 ms delay
    aec = EchoCanceller(noise_suppression=False)
    out = []
    for i in range(0, len(far), 320):
        aec.push_reference(far[i : i + 320])
        out.append(aec.process_capture(echo[i : i + 320]))
    cleaned = np.concatenate(out)
    tail = slice(-2 * sr, None)  # after convergence
    reduction_db = 10 * np.log10(np.mean(echo[tail] ** 2) / max(np.mean(cleaned[tail] ** 2), 1e-12))
    assert reduction_db > 20
