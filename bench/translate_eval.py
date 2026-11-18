"""Latency / quality / token use of translation backends (real API calls).

Feeds the test sentences in order with a running context, like the live pipeline.
Configs are interleaved per sentence so they see the same network conditions.

Usage: python translate_eval.py [rounds] [config ...]
  configs: haiku, haiku-adaptive, sonnet, opus, deepseek, deepseek-thinking
"""

import json
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from livecapture.translate import ClaudeBackend, DeepSeekBackend  # noqa: E402

HERE = Path(__file__).parent
CONFIGS = {
    "haiku": lambda: ClaudeBackend("claude-haiku-5-5", thinking_off=True),
    "haiku-adaptive": lambda: ClaudeBackend("claude-haiku-5-5", thinking_off=False),
    "sonnet": lambda: ClaudeBackend("claude-sonnet-5-5", thinking_off=True),
    "opus": lambda: ClaudeBackend("claude-opus-5-5"),
    "deepseek": lambda: DeepSeekBackend("deepseek-flash", thinking_off=True),
    "deepseek-thinking": lambda: DeepSeekBackend("deepseek-flash", thinking_off=False),
}


def sentences() -> list[tuple[str, str, str]]:
    """(text, src, target): German + English into Chinese, Chinese into English."""
    out = []
    de = json.loads((HERE / "audio" / "refs.json").read_text(encoding="utf-8"))
    cal = json.loads((HERE / "audio_calib" / "refs.json").read_text(encoding="utf-8"))
    zh = json.loads((HERE / "audio_zh" / "refs.json").read_text(encoding="utf-8"))
    split = lambda t: [s for s in re.split(r"(?<=[.!?。！？])\s*", t) if s.strip()]  # noqa: E731
    out += [(s, "de", "zh") for s in split(de["de"])[:4] + split(cal["de_calib2"])[:2]]
    out += [(s, "en", "zh") for s in split(de["en"])[2:5]]
    out += [(s, "zh", "en") for s in split(zh["zh2"])[:2]]
    return out


def main() -> None:
    args = sys.argv[1:]
    rounds = int(args.pop(0)) if args and args[0].isdigit() else 1
    names = args or ["haiku", "deepseek"]
    backends = {n: CONFIGS[n]() for n in names}
    for b in backends.values():
        b("Hallo.", "de", "zh", [])  # warm the connection
        b.usage.clear()
    sents = sentences()
    lat = {n: [] for n in names}
    outs = {n: [] for n in names}
    ctx = {n: [] for n in names}
    for r in range(rounds):
        for text, src, tgt in sents:
            for n, b in backends.items():
                t = time.perf_counter()
                tr = b(text, src, tgt, ctx[n][-6:])
                lat[n].append((time.perf_counter() - t) * 1000)
                ctx[n].append(("other", text, tr))
                if r == 0:
                    outs[n].append(tr)
    calls = rounds * len(sents)
    print(f"{calls} calls per config, interleaved\n")
    for n, b in backends.items():
        v = sorted(lat[n])
        print(f"{n:18s} mean {statistics.mean(v):5.0f} ms  median {statistics.median(v):5.0f}  "
              f"p90 {v[int(0.9 * (len(v) - 1))]:5.0f}  max {v[-1]:5.0f} | tokens/call in "
              f"{b.usage['input'] / calls:4.0f} out {b.usage['output'] / calls:3.0f}")
    print()
    for i, (text, src, tgt) in enumerate(sents):
        print(f"[{src}→{tgt}] {text}")
        for n in names:
            print(f"   {n:18s} {outs[n][i]}")


if __name__ == "__main__":
    main()
