"""Score the app against a recording that has subtitles (a film, a lecture with a transcript).

Record something with the app (● Record), put its subtitles next to it, then:

  python subtitle_eval.py run   <recording dir> --engine cpu     replay other.* through the real segmenter
  python subtitle_eval.py score <recording dir> <subtitles> cuda cpu live [--runs]

`run` writes <recording dir>/eval/<engine>.txt; `score` aligns those (and "live": what the app
wrote into captions.jsonl while recording) with the subtitle text, word by word. Times are
not used, so the subtitles need not be in sync, and the recording may be any part of the
film: the stretch of subtitles it covers is found by its text (from the first to the last
subtitle word that any listed transcript matches).

Subtitles: a Language Reactor export (.xlsx, columns Time / Subtitle) or a plain text file.
Subtitles are not word-for-word, so the absolute error rates are high - compare engines.
"""

import argparse
import html
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NUM = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven",
       "8": "eight", "9": "nine", "10": "ten"}


def norm_words(text: str) -> list[str]:
    text = text.lower().replace("’", "'")
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)  # [brass band plays], (sighs)
    words = re.findall(r"[^\W_]+(?:'[^\W_]+)*", text)
    return [NUM.get(w, w) for w in words]


def xlsx_column(path: Path, column: str = "B") -> list[str]:
    """One column of the first sheet, without the header row (no Excel library needed)."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in re.findall(r"<si>(.*?)</si>", z.read("xl/sharedStrings.xml").decode("utf-8"), re.S):
            shared.append(html.unescape("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S))))
    sheet = z.read(sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]).decode("utf-8")
    out = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
        for ref, attrs, body in re.findall(r'<c r="([A-Z]+)\d+"([^>]*?)(?:/>|>(.*?)</c>)', row, re.S):
            if ref != column:
                continue
            v = re.search(r"<v>(.*?)</v>", body or "", re.S)
            t = re.search(r"<t[^>]*>(.*?)</t>", body or "", re.S)
            out.append(shared[int(v.group(1))] if v and 't="s"' in attrs else html.unescape(t.group(1)) if t else "")
    return out[1:]


def reference(subs: Path) -> list[str]:
    lines = xlsx_column(subs) if subs.suffix.lower() == ".xlsx" else subs.read_text(encoding="utf-8").splitlines()
    return [w for line in lines if "♪" not in line for w in norm_words(line)]  # ♪: sung lyrics


def align(ref: list[str], hyp: list[str]) -> list[tuple[str, int, int]]:
    """Edit-distance alignment as (op, ref index, hyp index); op: = (same), s, d, i."""
    n, m = len(ref), len(hyp)
    prev = list(range(m + 1))
    back = [bytearray(m + 1) for _ in range(n + 1)]  # 0 diagonal, 1 deletion, 2 insertion
    for j in range(1, m + 1):
        back[0][j] = 2
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        back[i][0] = 1
        r, bi = ref[i - 1], back[i]
        for j in range(1, m + 1):
            best, op = prev[j - 1] + (r != hyp[j - 1]), 0
            if prev[j] + 1 < best:
                best, op = prev[j] + 1, 1
            if cur[j - 1] + 1 < best:
                best, op = cur[j - 1] + 1, 2
            cur[j], bi[j] = best, op
        prev = cur
    ops, i, j = [], n, m
    while i or j:
        op = back[i][j]
        if op == 0:
            ops.append(("=" if ref[i - 1] == hyp[j - 1] else "s", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif op == 1:
            ops.append(("d", i - 1, j))
            i -= 1
        else:
            ops.append(("i", i, j - 1))
            j -= 1
    return ops[::-1]


def locate(ref: list[str], hyp: list[str]) -> tuple[int, int]:
    """The slice of `ref` that `hyp` is a transcript of (generous at both ends), found by
    the three-word sequences they share."""
    grams = {tuple(hyp[i : i + 3]) for i in range(len(hyp) - 2)}
    hits = [tuple(ref[i : i + 3]) in grams for i in range(max(0, len(ref) - 2))]
    span = min(len(hits), int(len(hyp) * 1.3) + 50)
    total = sum(hits[:span])
    best, start = total, 0
    for i in range(1, len(hits) - span + 1):
        total += hits[i + span - 1] - hits[i - 1]
        if total > best:
            best, start = total, i
    return max(0, start - 50), min(len(ref), start + span + 50)


def anchors(ops: list[tuple[str, int, int]]) -> list[int]:
    """Reference positions inside runs of >= 3 matching words in a row. A single matching
    word far outside the recording (the alignment may park one there) is not an anchor."""
    out, run = [], []
    for op, i, _ in [*ops, ("x", 0, 0)]:
        if op == "=":
            run.append(i)
        else:
            if len(run) >= 3:
                out += run
            run = []
    return out


def transcript(rec: Path, name: str) -> list[str]:
    if name == "live":
        lines = [json.loads(line) for line in (rec / "captions.jsonl").read_text(encoding="utf-8").splitlines()]
        return norm_words(" ".join(c["committed"] for c in lines if c["stream"] == "other"))
    return norm_words((rec / "eval" / f"{name}.txt").read_text(encoding="utf-8"))


def score(rec: Path, subs: Path, names: list[str], show_runs: bool) -> None:
    ref_all = reference(subs)
    a, b = locate(ref_all, transcript(rec, names[0]))
    window = ref_all[a:b]
    matched = [i for name in names for i in anchors(align(window, transcript(rec, name)))]
    ref = window[min(matched) : max(matched) + 1]
    print(f"reference: {len(ref)} subtitle words (words {a + min(matched)}-{a + max(matched)} of {len(ref_all)})\n")
    print(f"{'transcript':24s} {'words':>6s} {'missing':>14s} {'wrong':>13s} {'extra':>12s} {'WER':>7s}   passages of >= 5 missing words")
    for name in names:
        hyp = transcript(rec, name)
        ops = align(ref, hyp)
        count = {k: sum(1 for op, *_ in ops if op == k) for k in "dsi"}
        runs, run = [], []
        for op, i, _ in [*ops, ("=", 0, 0)]:
            if op == "d":
                run.append(i)
            elif op != "i":
                if len(run) >= 5:
                    runs.append(run)
                run = []
        lost = sum(len(r) for r in runs)
        print(f"{name:24s} {len(hyp):6d} {count['d']:6d} ({count['d'] / len(ref):5.1%}) {count['s']:5d} ({count['s'] / len(ref):5.1%}) "
              f"{count['i']:5d} ({count['i'] / len(ref):4.1%}) {sum(count.values()) / len(ref):6.1%}   {len(runs)}, {lost} words ({lost / len(ref):.1%})")
        if show_runs:
            for r in runs:
                print(f"      @{r[0]:4d} ({len(r):2d}): {' '.join(ref[r[0]:r[-1] + 1])[:150]}")


def run(rec: Path, kind: str, tag: str | None) -> None:
    import soundfile as sf

    from livecapture import models
    from livecapture.segmenter import SR, SegmenterConfig, StreamProcessor
    from livecapture.stt import SttEngine
    from livecapture.vad import SileroVad

    engine = SttEngine(kind)
    audio, sr = sf.read(next(rec.glob("other.*")), dtype="float32")
    assert sr == SR
    cfg = SegmenterConfig(max_segment=min(12.0, engine.max_seconds - 1.0))
    finals: list[str] = []
    proc = StreamProcessor("other", engine, SileroVad(models.ensure_silero()), cfg,
                           lambda c: finals.append(c.committed) if c.final and c.committed else None, live=False)
    proc.start()
    for i in range(0, len(audio), 1600):
        proc.feed(audio[i : i + 1600])
    proc.stop()
    proc.join()
    (rec / "eval").mkdir(exist_ok=True)
    (rec / "eval" / f"{tag or kind}.txt").write_text("\n".join(finals), encoding="utf-8")
    mean = engine.stats()[0]
    print(f"{tag or kind}: {engine.device} | {len(audio) / sr:.0f} s | {len(finals)} captions, {sum(len(f.split()) for f in finals)} words, "
          f"{proc.forced_cuts} forced cuts, {proc.retries} retries, {proc.restored} words restored, {engine.calls} recognitions, mean {mean:.0f} ms")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("rec", type=Path)
    r.add_argument("--engine", default="cuda")
    r.add_argument("--tag")
    s = sub.add_parser("score")
    s.add_argument("rec", type=Path)
    s.add_argument("subs", type=Path)
    s.add_argument("names", nargs="+")
    s.add_argument("--runs", action="store_true", help="list the missing passages")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args.rec, args.engine, args.tag)
    else:
        score(args.rec, args.subs, args.names, args.runs)


if __name__ == "__main__":
    main()
