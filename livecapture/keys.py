"""Translation API keys: where they come from, and keys typed into the app.

Lookup order for a provider ("deepseek" / "anthropic" / "azure"):
  1. environment variable of this process (DEEPSEEK_API_KEY / ANTHROPIC_API_KEY / AZURE_TRANSLATOR_KEY)
  2. key saved in the app: ~/.livecapture/keys.json, encrypted with Windows DPAPI for the
     current Windows user (another user or machine cannot decrypt it)
  3. key bundled with a private "friends" build (edition.json, obfuscated only)
  4. user-level environment variable from the registry (`setx`; no re-login needed)
Keys are never sent to the page: the UI only learns where a key comes from and, for keys
the user entered, its last four characters.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes

from . import models
from .edition import EDITION

PROVIDERS = {"deepseek": "DEEPSEEK_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "azure": "AZURE_TRANSLATOR_KEY"}
REGIONS = {"azure": "AZURE_TRANSLATOR_REGION"}  # not secret: the app keeps it in settings.json
KEYS_FILE = models.APP_DIR / "keys.json"
_ENTROPY = b"mitschrift-live/keys"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf, ent = ctypes.create_string_buffer(data, len(data)), ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_ent = _Blob(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(blob_in), None, ctypes.byref(blob_ent), None, None, 0x1,  # UI_FORBIDDEN
            ctypes.byref(blob_out))
    if not ok:
        raise OSError(ctypes.get_last_error() or "DPAPI failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def user_env(name: str) -> str | None:
    """A user-level Windows environment variable straight from the registry.

    `setx NAME value` only reaches processes started afterwards; terminals that were
    already open (e.g. inside an IDE) would otherwise not see the key.
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            return winreg.QueryValueEx(k, name)[0] or None
    except OSError:
        return None


def _load() -> dict:
    try:
        return json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def saved(provider: str) -> str | None:
    blob = _load().get(provider)
    if not blob:
        return None
    try:
        return _dpapi(base64.b64decode(blob), protect=False).decode("utf-8")
    except (OSError, ValueError):
        return None  # saved by another Windows user / machine: ignore


def save(provider: str, key: str | None) -> None:
    """Store (or with an empty key, forget) the key typed into the app."""
    if provider not in PROVIDERS:
        raise ValueError(provider)
    data = _load()
    key = (key or "").strip()
    if key:
        data[provider] = base64.b64encode(_dpapi(key.encode("utf-8"), protect=True)).decode("ascii")
    else:
        data.pop(provider, None)
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEYS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _lookup(provider: str) -> tuple[str | None, str | None]:
    env = PROVIDERS[provider]
    for source, getter in (("env", lambda: os.environ.get(env)), ("saved", lambda: saved(provider)),
                           ("bundled", lambda: EDITION.key(provider)), ("user_env", lambda: user_env(env))):
        key = getter()
        if key:
            return key, source
    return None, None


def get(provider: str) -> str | None:
    return _lookup(provider)[0]


def region(provider: str) -> str | None:
    """A provider's region from the environment (process, then `setx`), if it needs one."""
    env = REGIONS.get(provider)
    return (os.environ.get(env) or user_env(env)) if env else None


def status() -> dict[str, dict]:
    """For the UI: where each provider's key comes from; last four characters only for keys
    the user typed or set themselves (a bundled key stays hidden)."""
    out = {}
    for p in PROVIDERS:
        key, source = _lookup(p)
        out[p] = {"source": source, "hint": key[-4:] if key and source != "bundled" else ""}
    return out
