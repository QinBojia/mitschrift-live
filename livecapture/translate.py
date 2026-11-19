"""Sentence-level translation of captions with Claude.

Sits between the stream processors (or the echo filter) and the display:
- partial captions pass straight through;
- once committed text contains complete sentences (long utterances are committed in
  pieces at forced cuts), those sentences are translated right away;
- a finished caption is shown as "translating..." until all its pieces are back, then
  forwarded with `translation` filled in.

What gets translated (target = "zh", "en", "de" or "off"): every sentence whose language
differs from the target - German, English and Chinese into any of the other two. Each
request carries the last few translated lines as context so names, pronouns and terms
stay consistent.
"""

from __future__ import annotations

import collections
import re
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from . import keys
from .i18n import tr
from .segmenter import Caption
from .stt import join_text

TARGET_NAMES = {"zh": "Simplified Chinese", "en": "English", "de": "German"}
LANG_NAMES = {"zh": "Chinese", "en": "English", "de": "German"}
SPEAKERS = {"other": "Remote", "me": "Me"}
PENDING = "…"  # translation placeholder while a request is in flight

_DE = set("der die das und ist nicht ich wir sie ein eine einen dem den des zu mit auf für von im auch es sich dass "
          "wie aber noch wenn so oder bei aus nach war sind haben habe hat wird werden kann können mal jetzt "
          "schon hier was wer warum gibt bitte danke ja nein".split())
_EN = set("the and is are to of in that it we you for with on this be have has not was what can will would "
          "should there their they our your about from just do does did so if but or at by an as yes no".split())
# CJK sentence ends need no following space; Latin ones must be followed by space/end
_SENT_END = re.compile(r"[。！？]+[”’」』）]*|[.!?…]+[\"'»”’)\]]*(?=\s|$)")


def detect_de_en(text: str) -> str:
    """Cheap German/English guess for Parakeet output (which carries no language tag)."""
    words = re.findall(r"[a-zäöüß]+", text.lower())
    de = sum(w in _DE for w in words) + 2 * sum(any(c in "äöüß" for c in w) for w in words)
    en = sum(w in _EN for w in words)
    return "en" if en > de else "de"


def complete_prefix(text: str) -> int:
    """Length of the prefix of `text` that ends with a complete sentence (0 if none)."""
    end = 0
    for m in _SENT_END.finditer(text):
        end = m.end()
    return end


class TranslationUnavailable(Exception):
    """Credentials missing/invalid: stop trying for the rest of the session."""


DEFAULT_SCENE = "a German language class"

SYSTEM = """You translate live captions. The captions come from automatic speech \
recognition, one sentence or fragment at a time.{scene}

Translate the text inside <current> into {target}.
- Reply with the translation only: no quotes, labels, notes or explanations.
- Speech recognition makes mistakes (misheard words, odd punctuation, a sentence cut in two). \
Translate what the speaker most plausibly meant; never comment on the errors.
- Use the lines in <context> only to resolve references, names and terminology; do not \
translate them again.
- Keep personal names, product names and numbers exact.
- If <current> is only a fragment, translate just the fragment; do not complete it.
- If <current> is already in {target}, repeat it unchanged."""


def _thinking_off(model: str) -> dict | None:
    """How to turn thinking off on a model (translation needs no reasoning); None if it can't be."""
    if model.startswith("claude-haiku-5-5"):
        return {"type": "disabled"}  # accepted at effort high or below
    if model.startswith("claude-sonnet-5-5"):
        return {"type": "between_tools"}  # Sonnet 5.5's thinking-off mode
    return None  # e.g. Claude Opus 5.5: thinking is always on; effort is the only lever


def system_prompt(target: str, scene: str | None = DEFAULT_SCENE) -> str:
    """The fixed instructions; `scene` (e.g. "a German language class") sets the register
    and helps with misheard domain words. Empty/None leaves it out."""
    setting = f"\n\nSetting: {scene.strip().rstrip('.')}." if scene and scene.strip() else ""
    return SYSTEM.format(target=TARGET_NAMES[target], scene=setting)


def user_prompt(text: str, src: str, context: list[tuple[str, str, str]]) -> str:
    ctx = "\n".join(f"{SPEAKERS.get(s, s)}: {a}\n→ {b}" for s, a, b in context)
    return (f"<context>\n{ctx}\n</context>\n" if ctx else "") + (
        f'<current language="{LANG_NAMES.get(src, src)}">{text}</current>'
    )


def make_backend(model: str, scene: str | None = DEFAULT_SCENE, azure_region: str = ""):
    """Pick the provider from the model name: deepseek-* -> DeepSeek, azure-translator ->
    Microsoft Translator, otherwise Claude."""
    if model.startswith("deepseek"):
        return DeepSeekBackend(model, scene=scene)
    if model == AzureBackend.model:
        return AzureBackend(azure_region)
    return ClaudeBackend(model, scene=scene)


class AzureBackend:
    """Microsoft Translator (Azure AI Translator, REST API v3): machine translation, not an
    LLM - about 0.1-0.3 s a sentence instead of ~0.7-0.9 s, but sentence by sentence: no
    context lines, no scene, and misrecognized words are translated as they are.

    Key: AZURE_TRANSLATOR_KEY (or Settings -> Translation). A resource created in a region
    (e.g. eastasia) also needs that region (settings, or AZURE_TRANSLATOR_REGION); a "global"
    one doesn't. Standard library HTTP with one kept-alive connection per worker thread (a new
    TLS connection per sentence would cost as much as the translation), through the system's
    HTTPS proxy if one is set."""

    model = "azure-translator"
    HOST = "api.cognitive.microsofttranslator.com"
    TO = {"zh": "zh-Hans", "en": "en", "de": "de"}

    def __init__(self, region: str = ""):
        import urllib.request

        key = keys.get("azure")
        if not key:
            raise TranslationUnavailable(tr("mt.no_key", provider="Microsoft Translator"))
        self.headers = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/json; charset=UTF-8"}
        region = (region or keys.region("azure") or "").strip()
        if region and region.lower() != "global":
            self.headers["Ocp-Apim-Subscription-Region"] = region
        self.proxy = urllib.request.getproxies().get("https")
        self._local = threading.local()
        self.usage = collections.Counter()  # characters sent (the service bills per character)

    def _connection(self):
        import http.client
        import urllib.parse

        c = getattr(self._local, "conn", None)
        if c is None:
            if self.proxy:
                p = urllib.parse.urlsplit(self.proxy if "://" in self.proxy else "http://" + self.proxy)
                c = http.client.HTTPSConnection(p.hostname, p.port or 80, timeout=10)
                c.set_tunnel(self.HOST, 443)
            else:
                c = http.client.HTTPSConnection(self.HOST, timeout=10)
            self._local.conn = c
        return c

    def __call__(self, text: str, src: str, target: str, context) -> str:
        import http.client
        import json
        import urllib.parse

        params = {"api-version": "3.0", "to": self.TO[target]}
        if src == "zh":  # SenseVoice is sure of Chinese; for de/en (a guess) the service detects
            params["from"] = "zh-Hans"
        path = "/translate?" + urllib.parse.urlencode(params)
        body = json.dumps([{"Text": text}], ensure_ascii=False).encode("utf-8")
        for attempt in (1, 2):  # a kept-alive connection the server has closed: once more on a new one
            c = self._connection()
            try:
                c.request("POST", path, body=body, headers=self.headers)
                r = c.getresponse()
                data = r.read()
                break
            except (http.client.HTTPException, OSError):
                c.close()
                self._local.conn = None
                if attempt == 2:
                    raise
        if r.status in (401, 403):
            if b"403001" in data:  # the free tier's 2 million characters of the month are used up
                raise TranslationUnavailable(tr("mt.quota", provider="Microsoft Translator"))
            raise TranslationUnavailable(tr("mt.bad_key", provider="Microsoft Translator", code=r.status))
        if r.status != 200:
            raise RuntimeError(f"Microsoft Translator {r.status}: {data[:200].decode('utf-8', 'replace')}")
        self.usage["chars"] += len(text)
        return json.loads(data)[0]["translations"][0]["text"].strip()


class ClaudeBackend:
    def __init__(self, model: str = "claude-haiku-5-5", effort: str = "low", thinking_off: bool = True,
                 scene: str | None = DEFAULT_SCENE):
        import anthropic

        self.scene = scene

        self._anthropic = anthropic
        self.thinking = _thinking_off(model) if thinking_off else None
        self.usage = collections.Counter()  # input/output tokens, for cost estimates
        key = keys.get("anthropic")
        # without a key the SDK falls back to ANTHROPIC_AUTH_TOKEN / an `ant auth login` profile
        self.client = anthropic.Anthropic(api_key=key, timeout=20.0, max_retries=2) if key else \
            anthropic.Anthropic(timeout=20.0, max_retries=2)
        self.model, self.effort = model, effort
        # Server-side refusal fallback ("default" routing) on models that support it.
        self.fallbacks = model.startswith(("claude-opus-5", "claude-sonnet-5-5", "claude-fable"))

    def __call__(self, text: str, src: str, target: str, context: list[tuple[str, str, str]]) -> str:
        kwargs = dict(
            model=self.model,
            max_tokens=4096,
            system=system_prompt(target, self.scene),
            messages=[{"role": "user", "content": user_prompt(text, src, context)}],
            output_config={"effort": self.effort},
        )
        if self.thinking:
            kwargs["thinking"] = self.thinking
        a = self._anthropic
        try:
            if self.fallbacks:
                resp = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
            else:
                resp = self.client.messages.create(**kwargs)
        except (a.AuthenticationError, a.PermissionDeniedError) as e:
            raise TranslationUnavailable(tr("mt.bad_key", provider="Claude", code=e.status_code)) from e
        except a.NotFoundError as e:
            raise TranslationUnavailable(tr("mt.no_model", model=self.model)) from e
        except TypeError as e:  # SDK: no credentials could be resolved
            if "auth" in str(e).lower():
                raise TranslationUnavailable(tr("mt.no_key", provider="Claude (Anthropic)")) from e
            raise
        except a.AnthropicError as e:
            if "authentication" in str(e).lower() or "api_key" in str(e).lower():
                raise TranslationUnavailable(tr("mt.no_key", provider="Claude (Anthropic)")) from e
            raise
        self.usage["input"] += resp.usage.input_tokens
        self.usage["output"] += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            return tr("mt.untranslated")
        return "".join(b.text for b in resp.content if b.type == "text").strip()


class DeepSeekBackend:
    """DeepSeek via its OpenAI-compatible API (key: DEEPSEEK_API_KEY). Same prompt as Claude."""

    BASE_URL = "https://api.deepseek.com"

    def __init__(self, model: str = "deepseek-flash", thinking_off: bool = True,
                 scene: str | None = DEFAULT_SCENE):
        import openai

        self.scene = scene

        self._openai = openai
        key = keys.get("deepseek")  # env var > typed into the app > bundled > setx (keys.py)
        if not key:
            raise TranslationUnavailable(tr("mt.no_key", provider="DeepSeek"))
        self.client = openai.OpenAI(api_key=key, base_url=self.BASE_URL, timeout=20.0, max_retries=2)
        self.model = model
        # deepseek-flash reasons briefly by default; translation doesn't need it
        self.extra_body = {"thinking": {"type": "disabled"}} if thinking_off else None
        self.usage = collections.Counter()

    def __call__(self, text: str, src: str, target: str, context: list[tuple[str, str, str]]) -> str:
        o = self._openai
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": system_prompt(target, self.scene)},
                    {"role": "user", "content": user_prompt(text, src, context)},
                ],
                extra_body=self.extra_body,
            )
        except (o.AuthenticationError, o.PermissionDeniedError) as e:
            raise TranslationUnavailable(tr("mt.bad_key", provider="DeepSeek", code=e.status_code)) from e
        except o.NotFoundError as e:
            raise TranslationUnavailable(tr("mt.no_model", model=self.model)) from e
        if resp.usage:
            self.usage["input"] += resp.usage.prompt_tokens
            self.usage["output"] += resp.usage.completion_tokens
        return (resp.choices[0].message.content or "").strip()


class DryRunBackend:
    """No API calls: tags the text. For testing the flow and the display."""

    def __init__(self, delay: float = 0.4):
        self.delay = delay
        self.model = "dry-run"

    def __call__(self, text: str, src: str, target: str, context) -> str:
        time.sleep(self.delay)
        return f"[{src}→{target}] {text}"


@dataclass
class _Utt:
    sent: int = 0  # chars of committed text already submitted
    parts: list[Future] = field(default_factory=list)
    final: Caption | None = None
    done: bool = False


def _resolved(value) -> Future:
    f: Future = Future()
    f.set_result(value)
    return f


class Translator:
    def __init__(self, sink: Callable[[Caption], None], backend, target: str = "zh", *,
                 workers: int = 3, context_size: int = 6):
        if target not in (*TARGET_NAMES, "off"):
            raise ValueError(f"target must be one of {[*TARGET_NAMES, 'off']}")
        self.sink, self.backend, self.target = sink, backend, target
        self._pool = ThreadPoolExecutor(workers, thread_name_prefix="translate")
        self._lock = threading.Lock()
        self._utts: dict[tuple[str, int], _Utt] = {}
        self._ctx: collections.deque[tuple[str, str, str]] = collections.deque(maxlen=context_size)
        self.timings: collections.deque[float] = collections.deque(maxlen=100)
        self.disabled = False
        self.error: str | None = None
        self.requests = 0

    # ---- caption flow -------------------------------------------------------
    def on_caption(self, c: Caption) -> None:
        key = (c.stream, c.utt_id)
        out: Caption
        new: list[Future] = []
        with self._lock:
            u = self._utts.setdefault(key, _Utt())
            if not c.final:
                end = complete_prefix(c.committed[u.sent :]) if len(c.committed) > u.sent else 0
                if end:
                    new += self._submit(u, c, c.committed[u.sent : u.sent + end])
                    u.sent += end
                out = c
            else:
                rest = c.committed[u.sent :]
                if rest.strip():
                    new += self._submit(u, c, rest)
                u.sent = len(c.committed)
                u.final = c
                if not c.committed or not u.parts:
                    self._utts.pop(key, None)
                    u.done = True
                    out = c
                else:  # keep the finished text on screen while translations run
                    out = replace(c, final=False, tentative="", translation=PENDING)
        self.sink(out)
        # Callbacks are attached outside the lock: a future that is already done runs its
        # callback immediately in this thread, which would otherwise re-enter the lock.
        for f in new:
            f.add_done_callback(lambda _f, key=key: self._maybe_finish(key))
        self._maybe_finish(key)

    def _submit(self, u: _Utt, c: Caption, text: str) -> list[Future]:
        src = "zh" if c.lang == "zh" else detect_de_en(text)
        target = self.target
        if target == "off" or src == target or self.disabled:
            u.parts.append(_resolved(None))
            return []
        try:
            fut = self._pool.submit(self._run, c.stream, text.strip(), src, target, self.backend)
        except RuntimeError:  # stopping: the pool is already shut down, the last sentence stays untranslated
            u.parts.append(_resolved(None))
            return []
        u.parts.append(fut)
        return [fut]

    # ---- live settings (called from the UI thread while captions flow) --------
    def set_target(self, target: str) -> None:
        """'zh' | 'en' | 'off'. Applies to sentences submitted from now on."""
        if target not in (*TARGET_NAMES, "off"):
            raise ValueError(target)
        if target != self.target:
            self._ctx.clear()  # earlier translations are in the other language
        self.target = target

    def set_backend(self, backend) -> None:
        self.backend = backend
        self.disabled, self.error = False, None  # a new key/model gets a fresh chance

    def _run(self, stream: str, text: str, src: str, target: str, backend) -> str | None:
        if self.disabled:
            return None
        t = time.perf_counter()
        try:
            out = backend(text, src, target, list(self._ctx))
        except TranslationUnavailable as e:
            self.disabled, self.error = True, str(e)
            return None
        except Exception as e:  # noqa: BLE001 - keep captions flowing whatever the API does
            self.error = tr("mt.error", err=type(e).__name__)
            return tr("mt.failed")
        self.timings.append((time.perf_counter() - t) * 1000)
        self.requests += 1
        self.error = None
        self._ctx.append((stream, text, out))
        return out

    def _maybe_finish(self, key) -> None:
        with self._lock:
            u = self._utts.get(key)
            if u is None or u.final is None or u.done or not all(f.done() for f in u.parts):
                return
            u.done = True
            del self._utts[key]
            pieces = [f.result() for f in u.parts]
            text = ""
            for p in pieces:
                if p:
                    text = join_text(text, p)
            out = replace(u.final, translation=text or None)
        self.sink(out)

    # ---- lifecycle / stats ----------------------------------------------------
    def flush(self, timeout: float = 15.0) -> None:
        """Wait (bounded) for in-flight translations so the last lines get printed."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not any(u.final is not None for u in self._utts.values()):
                    break
            time.sleep(0.05)
        self._pool.shutdown(wait=False, cancel_futures=True)

    def stats(self) -> tuple[float, float] | None:
        ts = sorted(self.timings.copy())
        if not ts:
            return None
        return statistics.mean(ts), ts[int(0.95 * (len(ts) - 1))]
