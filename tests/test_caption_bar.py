"""What the floating caption bar shows in its three display modes (caption_bar.entry_lines)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture.caption_bar import DEFAULTS, PENDING, SHOW_MODES, CaptionBar, entry_lines  # noqa: E402

SPEAKING = {"committed": "Guten Morgen", "tentative": "zusammen", "final": False, "translation": None}
WAITING = {"committed": "Guten Morgen zusammen.", "tentative": "", "final": True, "translation": PENDING}
DONE = {"committed": "Guten Morgen zusammen.", "tentative": "", "final": True, "translation": "大家早上好。"}
PLAIN = {"committed": "大家早上好。", "tentative": "", "final": True, "translation": None}  # nothing to translate


def test_both_shows_the_original_and_the_translation_under_it():
    main, line = entry_lines(DONE, "both")
    assert main[0] == ("Guten Morgen zusammen.", 1.0) and line == ("大家早上好。", 1.0)
    main, line = entry_lines(SPEAKING, "both")
    assert [t for t, _ in main] == ["Guten Morgen", " zusammen"] and main[1][1] < main[0][1] and line is None
    assert entry_lines(WAITING, "both")[1][1] < 1.0  # the "translating" note, dimmed


def test_original_never_shows_a_translation():
    for e in (SPEAKING, WAITING, DONE, PLAIN):
        main, line = entry_lines(e, "original")
        assert line is None and main[0] == (e["committed"], 1.0)


def test_translation_only_puts_the_translation_in_place_of_the_original():
    assert entry_lines(DONE, "translation") == ([("大家早上好。", 1.0)], None)


def test_translation_only_shows_the_original_dimmed_until_the_translation_arrives():
    for e in (SPEAKING, WAITING):
        main, line = entry_lines(e, "translation")
        assert line is None and main[0][0] == e["committed"] and main[0][1] < 1.0


def test_translation_only_keeps_the_original_when_there_is_no_translation():
    assert entry_lines(PLAIN, "translation") == ([("大家早上好。", 1.0), ("", 0.55)], None)


def test_saved_preferences_from_older_versions():
    make = lambda prefs: CaptionBar(prefs, None, lambda *a: None, lambda *a: None)  # noqa: E731
    assert DEFAULTS["show"] == "both" and SHOW_MODES == ("both", "original", "translation")
    assert make({"translation": False}).prefs["show"] == "original"  # the old on/off switch, off
    assert make({"translation": True}).prefs["show"] == "both"
    assert make({"show": "translation", "font": 30}).prefs == {**DEFAULTS, "show": "translation", "font": 30}
    assert make({"show": "nonsense"}).prefs["show"] == "both"
    bar = make({})
    seen = []
    bar._on_prefs = lambda prefs, geometry: seen.append(prefs["show"])  # noqa: SLF001
    for _ in range(3):
        bar._click("tr")  # noqa: SLF001 - the bar button cycles through the modes
    assert seen == ["original", "translation", "both"]
