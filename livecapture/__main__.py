"""Mitschrift Live, command line — live captions for system audio (对方) and microphone (我).

  python -m livecapture                       GPU engine, system audio + mic
  python -m livecapture --engine npu          Intel NPU (int8)
  python -m livecapture --sources other       system audio only
  python -m livecapture --list-devices        list audio devices
  python -m livecapture --check-devices       show input levels for 5 s (no STT)
  python -m livecapture --record recordings   also save audio + captions
  python -m livecapture --file talk.wav --plain --speed 2    replay a file
  python -m livecapture --target en           translate into English (default: Chinese)
  python -m livecapture --model claude-haiku-5-5            translate with Claude instead of DeepSeek
"""

from __future__ import annotations

import argparse
import dataclasses
import time

from rich.console import Console
from rich.live import Live

from . import STREAM_LABELS, models
from .audio import AudioSystem, FileSource, LiveSource, list_devices
from .display import LiveView, PlainPrinter
from .echo import EchoCanceller, EchoFilter
from .recorder import Recorder
from .segmenter import SegmenterConfig, StreamProcessor, rms_db
from .sensevoice import SenseVoice
from .stt import SttEngine
from .translate import DEFAULT_SCENE, DryRunBackend, Translator, make_backend
from .vad import SileroVad


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="livecapture", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=SttEngine.KINDS, default="cuda")
    ap.add_argument("--sources", default="other,me", help="comma list of: other (system audio), me (mic)")
    ap.add_argument("--speaker", help="loopback device index or name part (default: Windows default output)")
    ap.add_argument("--mic", help="microphone index or name part (default: Windows default input)")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--check-devices", action="store_true")
    ap.add_argument("--file", help="replay this audio file as the 'other' stream instead of live capture")
    ap.add_argument("--file-me", help="with --file: replay this file as the microphone stream ('me')")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed for --file")
    ap.add_argument("--no-aec", action="store_true", help="disable acoustic echo cancellation on the mic")
    ap.add_argument("--no-echo-filter", action="store_true", help="disable hiding echoed mic captions")
    ap.add_argument("--no-zh", action="store_true", help="disable Chinese detection/transcription (SenseVoice)")
    ap.add_argument("--target", choices=["zh", "en", "de"], default="zh", help="translation target language")
    # deepseek-flash: median ~0.73 s per sentence vs ~0.92 s Claude Haiku 5.5 (steadier),
    # 1.6 s Opus, 2.7 s Sonnet (bench/translate_eval.py); the user chose it for latency.
    ap.add_argument("--model", default="deepseek-flash",
                    help="translation model: deepseek-* (DEEPSEEK_API_KEY) or claude-* (ANTHROPIC_API_KEY); "
                         "default deepseek-flash")
    ap.add_argument("--scene", default=DEFAULT_SCENE,
                    help=f'setting given to the translator (default: "{DEFAULT_SCENE}"; "" to leave out)')
    ap.add_argument("--no-translate", action="store_true", help="captions only, no translation")
    ap.add_argument("--translate-dry-run", action="store_true", help="fake translations, no API calls")
    ap.add_argument("--plain", action="store_true", help="print finished lines only (no live view)")
    ap.add_argument("--record", metavar="DIR", help="save 16 kHz audio + captions under DIR/<timestamp>/")
    ap.add_argument("--min-silence", type=float, default=0.5, help="silence (s) that ends an utterance")
    ap.add_argument("--max-segment", type=float, help="force a cut after this many seconds of speech")
    return ap.parse_args()


def open_sources(args: argparse.Namespace) -> tuple[dict, AudioSystem | None]:
    if args.file:
        sources = {"other": FileSource(args.file, args.speed)}
        if args.file_me:
            sources["me"] = FileSource(args.file_me, args.speed)
        return sources, None
    system = AudioSystem()
    wanted = [s.strip() for s in args.sources.split(",") if s.strip()]
    sources = {}
    if "other" in wanted:
        sources["other"] = LiveSource(system, "loopback", args.speaker)
    if "me" in wanted:
        sources["me"] = LiveSource(system, "mic", args.mic)
    return sources, system


def check_devices(sources: dict) -> None:
    levels = {s: -100.0 for s in sources}
    for s, src in sources.items():
        src.start(lambda block, s=s: levels.__setitem__(s, max(levels[s], rms_db(block))))
        print(f"{STREAM_LABELS[s]}: {src.describe()}")
    for _ in range(10):
        time.sleep(0.5)
        print("   ".join(f"{STREAM_LABELS[s]} {db:6.1f} dB" for s, db in levels.items()), flush=True)
        for s in levels:
            levels[s] = -100.0
    for src in sources.values():
        src.stop()


def main() -> None:
    args = parse_args()
    if args.list_devices:
        print(list_devices())
        return
    sources, system = open_sources(args)
    try:
        if args.check_devices:
            check_devices(sources)
            return
        run(args, sources)
    finally:
        if system:
            system.close()


def run(args: argparse.Namespace, sources: dict) -> None:
    console = Console()
    for s, src in sources.items():
        console.print(f"[bold]{STREAM_LABELS[s]}[/] ← {src.describe()}")
    with console.status(f"加载识别模型 ({args.engine}) …"):
        engine = SttEngine(args.engine)
        vad_path = models.ensure_silero()
        sv = None if args.no_zh else SenseVoice(models.ensure_sensevoice())
    console.print(f"引擎: {engine.kind} · {engine.device}" + (" · 中文: SenseVoice (CPU)" if sv else " · 中文: 关"))

    cfg = SegmenterConfig(
        min_silence=args.min_silence,
        max_segment=args.max_segment or min(12.0, engine.max_seconds - 1.0),
    )
    recorder = Recorder(args.record, list(sources)) if args.record else None

    def on_caption(c) -> None:
        view.on_caption(c)
        if recorder:
            recorder.on_caption(c)

    # caption chain: processors -> echo filter -> translator -> display + recorder
    translator = None
    if not args.no_translate:
        try:
            backend = DryRunBackend() if args.translate_dry_run else make_backend(args.model, args.scene)
            translator = Translator(on_caption, backend, args.target)
        except Exception as e:  # noqa: BLE001 - captions still work without translation
            console.print(f"[yellow]翻译不可用: {e}[/]")
    view = PlainPrinter() if args.plain else LiveView(
        console, engine, {s: src.describe() for s, src in sources.items()}, translator)
    first = translator.on_caption if translator else on_caption

    both = "other" in sources and "me" in sources
    aec = None
    if both and not args.no_aec:
        try:
            aec = EchoCanceller()
        except ImportError:
            console.print("[yellow]未安装 livekit，回声消除关闭（pip install livekit）[/]")
    echo_filter = EchoFilter(first) if both and not args.no_echo_filter else None
    console.print(f"回声消除: {'开' if aec else '关'} · 回声文字过滤: {'开' if echo_filter else '关'} · 翻译: "
                  + (f"→{args.target} ({translator.backend.model})" if translator else "关")
                  + (f" · 场景: {args.scene}" if translator and args.scene else ""), style="dim")

    # With AEC, double-talk leaves short dips in the cleaned mic signal; a longer end-of-
    # utterance silence keeps those from chopping your sentences (bench/echo_eval.py).
    cfgs = {s: cfg for s in sources}
    if aec:
        cfgs["me"] = dataclasses.replace(cfg, min_silence=max(cfg.min_silence, 0.8))

    procs = {
        s: StreamProcessor(
            s, engine, SileroVad(vad_path), cfgs[s], echo_filter.on_caption if echo_filter else first,
            live=not args.file,
            on_level=getattr(view, "on_level", None),
            on_audio=recorder.on_audio if recorder else None,  # recorder keeps the raw (pre-AEC) mic
            sensevoice=sv,
        )
        for s in sources
    }
    for p in procs.values():
        p.start()
    feeds = {s: procs[s].feed for s in sources}
    if aec:
        other_feed, me_feed = feeds["other"], feeds["me"]

        def feed_other(block, _f=other_feed) -> None:
            aec.push_reference(block)
            _f(block)

        def feed_me(block, _f=me_feed) -> None:
            cleaned = aec.process_capture(block)
            if len(cleaned):
                _f(cleaned)

        feeds = {"other": feed_other, "me": feed_me}
    for s, src in sources.items():
        src.start(feeds[s])

    file_done = [src.done for src in sources.values()] if args.file else None

    def finished() -> bool:
        return bool(file_done) and all(d.is_set() for d in file_done)

    try:
        if args.plain:
            while not finished():
                time.sleep(0.1)
        else:
            with Live(view, console=console, refresh_per_second=10):
                while not finished():
                    view.lags.update({s: p.lag for s, p in procs.items()})
                    time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        for src in sources.values():
            src.stop()
        for p in procs.values():
            p.stop()
        for p in procs.values():
            p.join(timeout=10)
        if translator:
            translator.flush(15)  # let the last sentences' translations print
        if recorder:
            recorder.close()
            console.print(f"录音已保存: {recorder.dir}")
        stats = engine.stats()
        cuts = sum(p.forced_cuts for p in procs.values())
        if stats:
            echo = f" · 过滤回声 {echo_filter.suppressed} 句" if echo_filter else ""
            echo += f" · 中文 {sum(p.zh_utterances for p in procs.values())} 句" if sv else ""
            console.print(f"识别耗时 平均 {stats[0]:.0f} ms / p95 {stats[1]:.0f} ms · 强制切分 {cuts} 次{echo}", style="dim")
        if translator:
            ts = translator.stats()
            msg = f"翻译 {translator.requests} 次" + (f" · 平均 {ts[0]:.0f} ms / p95 {ts[1]:.0f} ms" if ts else "")
            console.print(msg + (f" · {translator.error}" if translator.error else ""), style="dim")


if __name__ == "__main__":
    main()
