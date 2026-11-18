"""Simulate a weaker PC (CPU engine with few threads) and check that live captions keep up.

Replays de.wav into "other" and en.wav into "me" in real time (2 streams, like a meeting),
with the adaptive partial interval on and off. Reports how far processing lags behind
the audio, how many live updates ran, and final WER.

Usage: python slow_cpu_eval.py [threads ...]
"""

import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from livecapture import models  # noqa: E402
from livecapture.audio import FileSource  # noqa: E402
from livecapture.segmenter import SegmenterConfig, StreamProcessor  # noqa: E402
from livecapture.sensevoice import SenseVoice  # noqa: E402
from livecapture.stt import SttEngine  # noqa: E402
from livecapture.vad import SileroVad  # noqa: E402
from parakeet_bench import wer  # noqa: E402

HERE = Path(__file__).parent


def run(threads: int, adaptive: bool) -> None:
    engine = SttEngine("cpu", threads=threads)
    sv = SenseVoice(models.ensure_sensevoice(), threads=max(1, threads // 2))
    refs = json.loads((HERE / "audio" / "refs.json").read_text(encoding="utf-8"))
    cfg = SegmenterConfig(adaptive_factor=4.0 if adaptive else 0.0)
    finals = {"other": [], "me": []}
    updates = {"other": 0, "me": 0}
    max_backlog = {"other": 0, "me": 0}

    def sink(c):
        if c.final and c.committed:
            finals[c.stream].append(c.committed)
        elif not c.final:
            updates[c.stream] += 1

    procs, srcs = {}, {}
    for stream, clip in (("other", "de"), ("me", "en")):
        procs[stream] = StreamProcessor(stream, engine, SileroVad(models.ensure_silero()), cfg, sink,
                                        live=False, sensevoice=sv)
        srcs[stream] = FileSource(HERE / "audio" / f"{clip}.wav", 1.0)
    for p in procs.values():
        p.start()
    t0 = time.time()
    for s, src in srcs.items():
        src.start(procs[s].feed)

    def watch():
        while not all(src.done.is_set() for src in srcs.values()):
            for s, p in procs.items():
                max_backlog[s] = max(max_backlog[s], p._q.qsize() * 0.02)  # noqa: SLF001 - 20 ms blocks
            time.sleep(0.1)

    w = threading.Thread(target=watch)
    w.start()
    w.join()
    audio_end = time.time()
    for p in procs.values():
        p.stop()
    for p in procs.values():
        p.join()
    tail = time.time() - audio_end
    st = engine.stats()
    print(f"threads={threads} adaptive={'on ' if adaptive else 'off'} | STT mean {st[0]:4.0f} ms | "
          f"max backlog other {max_backlog['other']:.1f}s me {max_backlog['me']:.1f}s | "
          f"finish {tail:.1f}s after audio | live updates {updates['other'] + updates['me']:3d} | "
          f"WER de {wer(refs['de'], ' '.join(finals['other'])):.1%} en {wer(refs['en'], ' '.join(finals['me'])):.1%} "
          f"| total {time.time() - t0:.0f}s")


if __name__ == "__main__":
    for th in [int(a) for a in sys.argv[1:]] or [1, 2]:
        for adaptive in (False, True):
            run(th, adaptive)
