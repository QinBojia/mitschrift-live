"""Run the echo scenario (make_echo_test.py) through the CLI with echo handling off/on.

Reports, for the mic stream ("我"): WER against the local English speech, and how many
far-end German words leaked in as echo.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from parakeet_bench import normalize, wer

HERE = Path(__file__).parent
ROOT = HERE.parent
CASES_ALL = {
    "no echo handling": ["--no-aec", "--no-echo-filter"],
    "text filter only": ["--no-aec"],
    "AEC only": ["--no-echo-filter"],
    "AEC + text filter": [],
}
CASES = {k: CASES_ALL[k] for k in (os.environ.get("ECHO_CASES") or ",".join(CASES_ALL)).split(",")}


def main() -> None:
    engine = sys.argv[1] if len(sys.argv) > 1 else "cuda"
    passthrough = sys.argv[2:]  # extra CLI args, e.g. --min-silence 0.8
    d = HERE / "audio_echo"
    refs = json.loads((d / "refs.json").read_text(encoding="utf-8"))
    german_only = set(normalize(refs["other"])) - set(normalize(refs["me"]))
    env = {**os.environ, "PYTHONUTF8": "1"}
    for name, extra in CASES.items():
        cmd = [sys.executable, "-m", "livecapture", "--engine", engine, "--plain", "--speed", "2",
               "--file", str(d / "other.wav"), "--file-me", str(d / "me.wav"), *extra, *passthrough]
        out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env).stdout
        lines = {"对方": [], "我": []}
        for m in re.finditer(r"^\[[\d:]+\] (对方|我): (.*)$", out, re.M):
            lines[m.group(1)].append(m.group(2))
        me = " ".join(lines["我"])
        leaked = [w for w in normalize(me) if w in german_only]
        print(f"\n=== {name}: 我 WER {wer(refs['me'], me):.1%} | German echo words in 我: {len(leaked)} "
              f"| 对方 WER {wer(refs['other'], ' '.join(lines['对方'])):.1%}")
        for t in lines["我"]:
            print(f"   我: {t}")
        tail = [ln for ln in out.splitlines() if "识别耗时" in ln]
        if tail:
            print("  ", tail[-1].strip())


if __name__ == "__main__":
    main()
