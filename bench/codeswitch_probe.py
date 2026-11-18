"""Probe: German words inside Chinese sentences.

Test audio is spliced: Chinese parts in a Chinese voice, German words in a German voice
(a Chinese TTS voice mispronounces German, which a real teacher would not).

Compares, per clip:
  SenseVoice alone                       (writes unknown German as English-like spelling)
  + Parakeet on each Latin span          (cut out the span's audio, re-recognize)
  Parakeet on the whole clip             (does it catch the German words in context?)

Usage: python codeswitch_probe.py [make] [cuda|npu|cpu]
"""

import asyncio
import io
import json
import sys
from pathlib import Path

import edge_tts
import numpy as np
import soundfile as sf
import soxr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from livecapture import models  # noqa: E402
from livecapture.sensevoice import SenseVoice  # noqa: E402
from livecapture.stt import SttEngine, is_cjk  # noqa: E402

SR = 16_000
HERE = Path(__file__).parent
OUT = HERE / "audio_codeswitch"
VOICE = {"zh": "zh-CN-YunxiNeural", "de": "de-DE-ConradNeural"}
CLIPS = {
    "cs1": [("zh", "好，我们先看一下第三课的生词。"), ("de", "Ehrenamt."),
            ("zh", "这个词的意思是志愿者的工作。它是中性名词，所以要说"), ("de", "das Ehrenamt.")],
    "cs2": [("zh", "这个句子里动词放在最后，因为前面有"), ("de", "weil"), ("zh", "，这是一个从句。")],
    "cs3": [("zh", "下一个词是"), ("de", "Fußballverein"), ("zh", "，就是足球俱乐部的意思。还有"),
            ("de", "Mitgliedsbeitrag"), ("zh", "，会费。")],
    "cs4": [("zh", "请大家注意"), ("de", "Vorstellung"), ("zh", "和"), ("de", "Veranstaltung"),
            ("zh", "这两个词很容易搞混。")],
}


async def tts(lang: str, text: str) -> np.ndarray:
    buf = io.BytesIO()
    async for ch in edge_tts.Communicate(text, VOICE[lang]).stream():
        if ch["type"] == "audio":
            buf.write(ch["data"])
    buf.seek(0)
    a, sr = sf.read(buf, dtype="float32")
    a = a.mean(axis=1) if a.ndim > 1 else a
    # trim leading/trailing silence so the splice sounds like one speaker turn
    idx = np.where(np.abs(a) > 0.01)[0]
    a = a[max(0, idx[0] - int(0.03 * sr)) : idx[-1] + int(0.05 * sr)] if len(idx) else a
    return soxr.resample(a, sr, SR).astype(np.float32)


async def make() -> None:
    OUT.mkdir(exist_ok=True)
    refs = {}
    for name, parts in CLIPS.items():
        pieces = []
        for lang, text in parts:
            pieces += [await tts(lang, text), np.zeros(int(0.08 * SR), np.float32)]
        sf.write(OUT / f"{name}.wav", np.concatenate(pieces), SR, subtype="PCM_16")
        refs[name] = "".join(t if lang == "zh" else f" {t} " for lang, t in parts).strip()
    (OUT / "refs.json").write_text(json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")


def latin_spans(words):
    spans, cur = [], []
    for w in words:
        if any(c.isascii() and c.isalpha() for c in w.text) and not any(is_cjk(c) for c in w.text):
            cur.append(w)
        elif cur:
            spans.append(cur)
            cur = []
    return spans + ([cur] if cur else [])


def main() -> None:
    args = sys.argv[1:]
    if "make" in args:
        asyncio.run(make())
        args.remove("make")
    sv = SenseVoice(models.ensure_sensevoice())
    pk = SttEngine(args[0] if args else "cuda")
    refs = json.loads((OUT / "refs.json").read_text(encoding="utf-8"))
    for name, ref in refs.items():
        a = sf.read(OUT / f"{name}.wav", dtype="float32")[0]
        tr, _ = sv.recognize(a)
        print(f"\n[{name}] reference : {ref}")
        print(f"         SenseVoice: {tr.text}")
        print(f"         Parakeet  : {pk.transcribe(a).text}")
        for span in latin_spans(tr.words):
            s = max(0, int((span[0].start - 0.25) * SR))
            e = min(len(a), int((span[-1].last + 0.5) * SR))
            seg = np.concatenate([np.zeros(int(0.2 * SR), np.float32), a[s:e]])
            print(f"         span {span[0].start:4.1f}-{span[-1].last:4.1f}s "
                  f"'{' '.join(w.text for w in span)}' -> Parakeet '{pk.transcribe(seg).text}'")


if __name__ == "__main__":
    main()
