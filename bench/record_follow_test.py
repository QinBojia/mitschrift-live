"""Backend checks without a window: (1) recording during a file replay, (2) restart when
the Windows default device changes (simulated) during live capture.

Usage: python record_follow_test.py
"""

import json
import sys
import tempfile
import time
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from livecapture.pipeline import Settings  # noqa: E402
from livecapture.server import Backend  # noqa: E402


def wait(pred, timeout: float) -> bool:
    t = time.time()
    while time.time() - t < timeout:
        if pred():
            return True
        time.sleep(0.2)
    return False


def recording_test(tmp: Path) -> None:
    cfg = tmp / "s1.json"
    Settings(engine="cuda", file=str(ROOT / "bench" / "audio" / "de.wav"), speed=2.0, sources=["other"],
             target="zh").save(cfg)
    b = Backend(cfg)
    b.record_dir = tmp / "rec"
    b.set("record", True)
    b.start()
    ok = wait(lambda: b.pipeline.files_done, 60)
    time.sleep(2)
    b.stop()
    d = Path(b.last_recording)
    wav = d / "other.wav"
    lines = (d / "captions.jsonl").read_text(encoding="utf-8").splitlines()
    info = sf.info(wav)
    print(f"[record] replay done={ok} folder={d.name} other.wav={info.duration:.1f}s "
          f"me.wav exists={(d / 'me.wav').exists()} captions={len(lines)}")
    print(f"         first: {json.loads(lines[0])['committed'][:50]} -> {json.loads(lines[0])['translation']}")
    b.shutdown()


def follow_test(tmp: Path) -> None:
    cfg = tmp / "s2.json"
    Settings(engine="cuda", target="off").save(cfg)  # live capture, default devices
    b = Backend(cfg)
    b.start()
    print(f"[follow] running={b.pipeline.running} sources={b.pipeline.describe_sources()}")
    first = b.pipeline
    mic = first.describe_sources().get("me", "")
    b._on_default_devices_changed("Some Other Speaker", mic)  # simulate: default output changed
    st = b._status()
    print(f"[follow] restarted={b.pipeline is not first} running={st['running']} notice={st['notice']!r} "
          f"error={st['error']}")
    time.sleep(3)
    print(f"[follow] still capturing: levels={ {k: round(v) for k, v in b.levels.items()} } session={b.session}")
    b.stop()
    b.shutdown()


if __name__ == "__main__":
    tmp = Path(tempfile.mkdtemp())
    recording_test(tmp)
    follow_test(tmp)
