"""Compare translation backends on real captions: speed, and the translations side by side.

Takes the finished captions of a recording (captions.jsonl), translates a stretch of them with
each backend - in order, with the running context the live app keeps (the LLMs use it,
Microsoft Translator has none), backends interleaved per sentence so they share the network
conditions - and prints the latency per backend. The translations go to
<recording>/eval/mt_compare.md for reading (recording folders stay out of the repository).

  python mt_compare.py <recording dir> [--target zh] [--n 40] [--start 0.3] [--models deepseek-flash azure-translator]

Keys as in the app (environment, keys saved in the app, setx); Microsoft Translator also needs
AZURE_TRANSLATOR_REGION for a regional resource.
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from livecapture import keys  # noqa: E402
from livecapture.translate import detect_de_en, make_backend  # noqa: E402


def captions(rec: Path) -> list[tuple[str, str, str]]:
    """(stream, text, src) of the recording's finished captions, in order of their start."""
    rows = [json.loads(line) for line in (rec / "captions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    rows.sort(key=lambda r: r["start"])
    out = []
    for r in rows:
        text = r["committed"].strip()
        if len(text.split()) >= 2 or r.get("lang") == "zh":
            src = r.get("src") or ("zh" if r.get("lang") == "zh" else detect_de_en(text))
            out.append((r["stream"], text, src))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("rec", type=Path)
    ap.add_argument("--target", default="zh")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--start", type=float, default=0.3, help="where the stretch starts (fraction of the recording)")
    ap.add_argument("--models", nargs="+", default=["deepseek-flash", "azure-translator"])
    args = ap.parse_args()

    caps = captions(args.rec)
    i0 = int(len(caps) * args.start)
    caps = [c for c in caps[i0:] if c[2] != args.target][: args.n]
    backends = {m: make_backend(m, "a TV drama", keys.region("azure") or "") for m in args.models}
    ctx = {m: [] for m in args.models}  # (stream, original, translation), the last 6, like the app
    times = {m: [] for m in args.models}
    out = {m: [] for m in args.models}
    for m, b in backends.items():  # warm up: the first call opens the connection
        b("Guten Tag.", "de", args.target, [])
    for k, (stream, text, src) in enumerate(caps):
        for m in (args.models if k % 2 == 0 else args.models[::-1]):
            t = time.perf_counter()
            try:
                tr = backends[m](text, src, args.target, ctx[m][-6:])
            except Exception as e:  # noqa: BLE001 - show it in the table
                tr = f"[{type(e).__name__}: {e}]"
            times[m].append((time.perf_counter() - t) * 1000)
            out[m].append(tr)
            ctx[m].append((stream, text, tr))
    print(f"{len(caps)} captions from {args.rec.name} -> {args.target}")
    for m in args.models:
        ts = sorted(times[m])
        print(f"  {m:18s} median {statistics.median(ts):5.0f} ms   p90 {ts[int(len(ts) * 0.9)]:5.0f} ms   max {ts[-1]:5.0f} ms")
    lines = [f"# {args.rec.name}: {len(caps)} captions -> {args.target}", ""]
    lines += ["| # | " + " | ".join(["original", *args.models]) + " |", "|---" * (len(args.models) + 2) + "|"]
    for k, (_, text, _) in enumerate(caps):
        cells = [text, *(out[m][k] for m in args.models)]
        lines.append(f"| {k + 1} | " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    (args.rec / "eval").mkdir(exist_ok=True)
    path = args.rec / "eval" / "mt_compare.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"side by side: {path}")


if __name__ == "__main__":
    main()
