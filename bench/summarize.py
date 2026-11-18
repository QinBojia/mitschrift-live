"""Print a comparison table of all benchmark results in bench/results/*.json."""

import json
import sys
from pathlib import Path

rows = []
for f in sorted((Path(__file__).parent / "results").glob("*.json")):
    if len(sys.argv) > 1 and f.stem not in sys.argv[1:]:
        continue
    r = json.loads(f.read_text(encoding="utf-8"))
    lat = {x["seconds"]: x["median_ms"] for x in r["latency"]}
    rt = r["realtime"]
    rows.append([
        r["tag"],
        f"{r['accuracy']['de']['wer']:.1%}",
        f"{r['accuracy']['en']['wer']:.1%}",
        lat.get(2, "-"),
        lat.get(10, "-"),
        f"{rt['duty_cycle']:.2f}",
        f"{rt['late_ticks']}/{rt['ticks']}",
        rt["lat_median_ms"],
        rt["lat_p95_ms"],
        rt["proc_cpu_pct_of_machine"],
        rt["rss_mib"],
    ])

head = ["config", "WER de", "WER en", "2s ms", "10s ms", "duty", "late", "rt med", "rt p95", "CPU%", "RSS MiB"]
w = [max(len(str(x)) for x in col) for col in zip(head, *rows)]
for row in [head, *rows]:
    print("  ".join(str(x).rjust(n) for x, n in zip(row, w)))
