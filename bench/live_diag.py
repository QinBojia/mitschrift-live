"""Run the live CLI with a status line every 2 s per stream and thread-crash reporting.

Usage: python live_diag.py <seconds> [livecapture args...]
"""

import faulthandler
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import livecapture.__main__ as cli  # noqa: E402
import livecapture.segmenter as seg  # noqa: E402

SR = 16_000
procs: list = []
_orig_init = seg.StreamProcessor.__init__


def _init(self, *a, **kw):
    _orig_init(self, *a, **kw)
    procs.append(self)


seg.StreamProcessor.__init__ = _init
cli.StreamProcessor = seg.StreamProcessor
if "--tty" in sys.argv:  # make rich behave as in a real terminal even when output is captured
    sys.argv.remove("--tty")
    from rich.console import Console

    cli.Console = lambda: Console(force_terminal=True, force_interactive=True, width=160)


def _excepthook(args):
    print(f"!!! thread {args.thread.name} crashed:", flush=True)
    traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)


threading.excepthook = _excepthook


def monitor(t0: float) -> None:
    last_pos: dict[str, tuple[int, float]] = {}
    dumped = False
    while True:
        time.sleep(2)
        for p in procs:  # a stream whose audio position stops while audio keeps queueing is hung
            pos, since = last_pos.get(p.stream, (-1, time.time()))
            if p._pos != pos:  # noqa: SLF001
                last_pos[p.stream] = (p._pos, time.time())  # noqa: SLF001
            elif time.time() - since > 4 and p._q.qsize() > 50 and not dumped:  # noqa: SLF001
                print(f"!!! {p.stream} stalled; thread stacks:", flush=True)
                faulthandler.dump_traceback(all_threads=True)
                dumped = True
        parts = []
        for p in procs:
            st = "speech" if p._in_speech else "idle"  # noqa: SLF001
            buf = p._buf_len / SR if p._in_speech else 0  # noqa: SLF001
            parts.append(
                f"{p.stream}: alive={p.is_alive()} q={p._q.qsize()} lag={p.lag:.1f}s "  # noqa: SLF001
                f"audio={p._pos / SR:.0f}s {st} buf={buf:.1f}s cuts={p.forced_cuts} retries={p.retries}"  # noqa: SLF001
            )
        if procs:
            stats = procs[0].engine.stats()
            eng = f"stt n={len(procs[0].engine.timings)} mean={stats[0]:.0f}ms" if stats else "stt -"
            print(f"  [diag {time.time() - t0:5.1f}s] " + " | ".join(parts) + f" | {eng}", flush=True)


if __name__ == "__main__":
    seconds = float(sys.argv[1])
    sys.argv = ["livecapture", *sys.argv[2:]]
    import _thread

    threading.Timer(seconds, _thread.interrupt_main).start()
    threading.Thread(target=monitor, args=(time.time(),), daemon=True).start()
    cli.main()
