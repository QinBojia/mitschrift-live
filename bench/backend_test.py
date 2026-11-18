"""End-to-end test of the desktop backend over its WebSocket, without any window.

Replays bench/results/mixed.wav (German / Chinese / English) through the Backend, switches
the translation target to English half-way, and checks what a page would receive.

Usage: python backend_test.py [engine]
"""

import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from websockets.exceptions import InvalidStatus  # noqa: E402
from websockets.sync.client import connect  # noqa: E402

from livecapture.pipeline import Settings  # noqa: E402
from livecapture.server import Backend  # noqa: E402


def main() -> None:
    engine = sys.argv[1] if len(sys.argv) > 1 else "cuda"
    cfg = Path(tempfile.mkdtemp()) / "settings.json"
    Settings(engine=engine, file=str(ROOT / "bench" / "results" / "mixed.wav"), speed=3.0,
             sources=["other"], target="zh", model="deepseek-flash").save(cfg)
    backend = Backend(cfg)
    port = backend.serve()
    print(f"backend on 127.0.0.1:{port}")

    try:
        connect(f"ws://127.0.0.1:{port}/ws?token=wrong", open_timeout=5)
        print("FAIL: wrong token accepted")
    except InvalidStatus as e:
        print("wrong token rejected:", e.response.status_code)

    kinds = Counter()
    finals, loading, statuses = [], [], []
    with connect(f"ws://127.0.0.1:{port}/ws?token={backend.token}") as ws:
        ws.send(json.dumps({"type": "control", "action": "start"}))
        t0 = time.time()
        switched = was_running = False
        while time.time() - t0 < 240:
            try:
                m = json.loads(ws.recv(timeout=1))
            except TimeoutError:
                continue
            kinds[m["type"]] += 1
            if m["type"] == "status":
                statuses.append(m)
                if m.get("loading") and m["loading"] not in loading:
                    loading.append(m["loading"])
                was_running |= m["running"]
                if was_running and not m["running"] and not m.get("loading"):
                    break  # replay finished and the backend stopped itself
            elif m["type"] == "caption" and m["final"]:
                finals.append(m)
                print(f"  [{m['src']}] {m['committed'][:60]}\n      -> {m['translation']}")
            if not switched and time.time() - t0 > 35:
                ws.send(json.dumps({"type": "set", "key": "target", "value": "en"}))
                switched = True
                print("  --- target switched to en ---")
    backend.shutdown()

    last = statuses[-1]
    print(f"\nmessages: {dict(kinds)}")
    print(f"loading steps: {loading}")
    print(f"finals: {len(finals)}, translated: {sum(1 for f in finals if f['translation'])}, "
          f"src: {dict(Counter(f['src'] for f in finals))}")
    print(f"last status: engine={last['engine']} model={last['model']} target={last['target']} "
          f"asr_ms={last['asr_ms']} mt_ms={last['mt_ms']} error={last['error']}")
    print(f"saved target in settings file: {json.loads(cfg.read_text(encoding='utf-8'))['target']}")


if __name__ == "__main__":
    main()
