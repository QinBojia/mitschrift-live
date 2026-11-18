"""Both interface languages have the same strings, and every string the page asks for exists."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from livecapture import i18n  # noqa: E402


def page_tables() -> dict[str, set[str]]:
    js = (ROOT / "ui" / "i18n.js").read_text(encoding="utf-8")
    tables = {}
    for lang in ("zh", "en"):
        block = re.search(rf"\n  {lang}: \{{(.*?)\n  \}},", js, re.S).group(1)
        tables[lang] = set(re.findall(r'"([\w.]+)":', block))
    return tables


def test_python_strings_match():
    assert set(i18n.STRINGS["zh"]) == set(i18n.STRINGS["en"])


def test_page_strings_match():
    t = page_tables()
    assert t["zh"] == t["en"], t["zh"] ^ t["en"]


def test_page_uses_only_known_keys():
    keys = page_tables()["zh"]
    html = (ROOT / "ui" / "main.html").read_text(encoding="utf-8")
    used = set(re.findall(r'data-i18n(?:-html|-title|-ph)?="([\w.]+)"', html))
    js = (ROOT / "ui" / "main.js").read_text(encoding="utf-8") + (ROOT / "ui" / "client.js").read_text(encoding="utf-8")
    used |= set(re.findall(r'\bt\("([\w.]+)"', js))
    assert used - keys == set()


def test_tr_formats_and_falls_back():
    i18n.set_lang("en")
    assert i18n.tr("loading.engine", label="CPU") == "Loading the speech model (CPU)…"
    i18n.set_lang("zh")
    assert i18n.tr("who.me") == "我"
