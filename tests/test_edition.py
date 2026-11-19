"""Editions: every one offers Claude Haiku and Microsoft Translator, also an install whose
edition.json predates that."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture.edition import Edition, load  # noqa: E402


def test_a_deepseek_only_edition_also_offers_claude_haiku_and_microsoft_translator(tmp_path):
    p = tmp_path / "edition.json"
    p.write_text(json.dumps({"name": "friends-npu", "models": ["deepseek-flash"], "default_model": "deepseek-flash"}))
    ed = load(p)
    assert ed.models == ("deepseek-flash", "claude-haiku-5-5", "azure-translator")
    assert ed.default_model == "deepseek-flash"
    assert ed.key("anthropic") is None and ed.key("azure") is None  # the user's own keys: none is bundled


def test_editions_without_a_model_list_stay_open(tmp_path):
    p = tmp_path / "edition.json"
    p.write_text(json.dumps({"name": "public-cpu", "engines": ["cpu"], "engine": "cpu"}))
    assert load(p).models is None and load(tmp_path / "missing.json") == Edition()
