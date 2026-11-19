"""What the floating caption bar shows: its three display modes (caption_bar.entry_lines) and
how many captions fit (caption_bar.layout)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture.caption_bar import (  # noqa: E402
    DEFAULTS,
    PENDING,
    SHOW_MODES,
    CaptionBar,
    entry_lines,
    layout,
    render,
    speaker_color,
)

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
    assert make({"show": "translation", "font": 30, "font_v": 2}).prefs == {**DEFAULTS, "show": "translation", "font": 30}
    # up to 0.3.2 "font" was the newest line's size and earlier lines got 0.78 of it: now every
    # line has the size the earlier ones had
    assert make({"font": 26}).prefs["font"] == 20 and make({"font": 48}).prefs["font"] == 37
    assert make({"show": "nonsense"}).prefs["show"] == "both"
    bar = make({})
    seen = []
    bar._on_prefs = lambda prefs, geometry: seen.append(prefs["show"])  # noqa: SLF001
    for _ in range(3):
        bar._click("tr")  # noqa: SLF001 - the bar button cycles through the modes
    assert seen == ["original", "translation", "both"]


def _history(n: int) -> list[dict]:
    """n finished captions, alternating speakers in runs of three, with translations."""
    return [{"stream": "other" if i // 3 % 2 == 0 else "me", "utt": str(i), "final": True, "tentative": "",
             "committed": f"Satz Nummer {i} ist kurz.", "translation": f"第 {i} 句很短。"} for i in range(n)]


def _texts(rows) -> list[str]:
    return ["".join(u for _, u, _, _ in row[3]) for row in rows]


def test_a_taller_bar_shows_more_of_the_history_and_the_newest_at_the_bottom():
    entries = _history(30)
    short = layout(1000, 200, 1.0, entries, DEFAULTS)
    tall = layout(1000, 600, 1.0, entries, DEFAULTS)
    assert len(tall) > 2 * len(short)
    for rows in (short, tall):
        bottom = max(rows, key=lambda r: r[0])
        assert _texts([bottom]) == ["第 29 句很短。"]  # the newest caption's translation, last line
    assert "Satz Nummer 24 ist kurz." in _texts(tall) and "Satz Nummer 24 ist kurz." not in _texts(short)


def test_only_whole_lines_inside_the_bar():
    for h in (120, 200, 333, 600):
        for y, _, lh, _, _ in layout(1000, h, 1.0, _history(30), DEFAULTS):
            assert y >= 0 and y + lh <= h


def test_every_line_has_one_size_and_a_bar_in_the_speakers_colour():
    entries = _history(9)
    entries[-1] = {**entries[-1], "translation": PENDING}
    for show in SHOW_MODES:
        rows = layout(1000, 2000, 1.5, entries, {**DEFAULTS, "show": show})  # tall enough for all of them
        assert {r[1].size for r in rows} == {30} and len({r[2] for r in rows}) == 1  # 20 px at 150 %
        assert all(t.startswith("Satz") or "句" in t or t == "翻译中…" or t == "Translating…" for t in _texts(rows))
        for _, _, _, _, (i, color) in rows:  # no label lines: the bar says who spoke
            assert color == speaker_color(entries[i])
    rows = layout(1000, 2000, 1.0, entries, DEFAULTS)
    bold = {_texts([r])[0]: "bd" in Path(r[1].path).name for r in rows}
    assert bold["Satz Nummer 0 ist kurz."] and not bold["第 0 句很短。"]  # original bold, translation regular


def test_an_empty_bar_says_it_is_waiting_and_frames_have_the_window_size():
    assert len(layout(800, 150, 1.0, [], DEFAULTS)) == 1
    for hover in (False, True):
        data, rects = render(800, 300, 1.5, _history(12), {**DEFAULTS, "bg_alpha": 0.3}, hover=hover, hot=None, locked=False)
        assert len(data) == 800 * 300 * 4 and bool(rects) == hover
