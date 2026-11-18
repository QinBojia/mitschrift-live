"""Build edition: settings baked into a packaged build (e.g. the friends' installer).

A packaged build has `edition.json` next to the `livecapture` package (APP_ROOT). A dev
checkout has none and gets the full-featured defaults.

The bundled API key is only obfuscated, not secret: anyone with the files can recover
it. Use a dedicated key for a build you hand out, so it can be revoked on its own.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
_MASK = b"live-capture/friends"


@dataclass(frozen=True)
class Edition:
    name: str = "dev"
    engines: tuple[str, ...] | None = None  # allowed speech engines; None = any
    engine: str | None = None  # default engine for this edition
    models: tuple[str, ...] | None = None  # allowed translation models; None = any
    default_model: str | None = None
    deepseek_key: str | None = None  # obfuscated
    ui_lang: str | None = None  # default interface language ("zh" / "en"); None = follow Windows

    @property
    def default_ui_lang(self) -> str:
        """Friends' builds are for Chinese speakers, whatever language their Windows shows
        (also builds made before edition.json had ui_lang); everyone else: follow Windows."""
        return self.ui_lang or ("zh" if self.name.startswith("friends") else "auto")

    @property
    def locked_engine(self) -> bool:
        return self.engines is not None and len(self.engines) <= 1

    def key(self, provider: str) -> str | None:
        if provider == "deepseek" and self.deepseek_key:
            return reveal(self.deepseek_key)
        return None


def hide(secret: str) -> str:
    raw = secret.encode()
    return base64.b64encode(bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(raw))).decode()


def reveal(blob: str) -> str:
    raw = base64.b64decode(blob)
    return bytes(b ^ _MASK[i % len(_MASK)] for i, b in enumerate(raw)).decode()


def load(path: Path = APP_ROOT / "edition.json") -> Edition:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Edition()
    for k in ("models", "engines"):
        if data.get(k) is not None:
            data[k] = tuple(data[k])
    return Edition(**{k: v for k, v in data.items() if k in Edition.__dataclass_fields__})


EDITION = load()
