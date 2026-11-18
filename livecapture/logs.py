"""Log file for the windowed app.

Started from a shortcut (pythonw.exe) there is no console: sys.stdout/stderr are None and
anything printed or raised would vanish. Send it all to ~/.livecapture/logs/ instead, so a
friend can send the file when something goes wrong.

faulthandler stays OFF unless LIVECAPTURE_FAULTHANDLER=1. On Windows it also fires on
exceptions that Windows raises and handles itself (COM/RPC errors such as 0x800706BA, seen
with WebView2 / the audio stack), dumping every thread each time; doing that while the audio
threads run crashed the app with an access violation (0.1.4). Python exceptions still reach
the log through stderr.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import platform
import sys
import time
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024


def setup(app_dir: Path, version: str, edition: str) -> Path:
    log_dir = app_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "live-capture.log"
    if path.exists() and path.stat().st_size > MAX_BYTES:
        path.replace(log_dir / "live-capture.1.log")
    windowed = sys.stdout is None or sys.stderr is None
    if windowed:
        f = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives as long as the app
        sys.stdout = sys.stderr = f
        if os.environ.get("LIVECAPTURE_FAULTHANDLER") == "1":  # debugging only, see above
            faulthandler.enable(file=f, all_threads=False)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"\n===== Mitschrift Live {version} ({edition}) {time.strftime('%Y-%m-%d %H:%M:%S')} "
          f"| Windows {platform.version()} | Python {platform.python_version()} =====", flush=True)
    return path
