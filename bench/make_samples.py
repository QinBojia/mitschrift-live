"""Generate German / English test speech with edge-tts, saved as 16 kHz mono WAV.

Usage: python make_samples.py <out_dir> [test|calib|zh]
"""

import asyncio
import io
import json
import sys
from pathlib import Path

import edge_tts
import numpy as np
import soundfile as sf

SAMPLES = {
    "de": (
        "de-DE-ConradNeural",
        "Guten Morgen zusammen, danke, dass ihr euch heute Zeit genommen habt. "
        "Wir wollen zuerst kurz auf die Ergebnisse des letzten Quartals schauen und danach "
        "über die Planung für das nächste Jahr sprechen. Der Umsatz ist im Vergleich zum "
        "Vorjahr um ungefähr zwölf Prozent gestiegen, allerdings sind auch die Kosten für "
        "die Logistik deutlich höher, als wir ursprünglich erwartet hatten. "
        "Ich würde vorschlagen, dass Frau Schneider die Zahlen im Detail vorstellt, "
        "bevor wir in die Diskussion einsteigen. Gibt es dazu Fragen oder Einwände?",
    ),
    "en": (
        "en-US-AndrewNeural",
        "Thanks everyone for joining. Before we dive into the roadmap, I want to quickly "
        "recap where we landed last week. The prototype is working end to end, but latency "
        "is still higher than we would like, especially when the network is unstable. "
        "Our plan is to move the speech recognition onto the local GPU and keep only the "
        "translation step in the cloud. If that works, we should be able to bring the "
        "delay down to around one or two seconds. Does anyone have concerns about that approach?",
    ),
}


# Separate material for int8 calibration (never used for accuracy scoring).
CALIB = {
    "de_calib1": (
        "de-DE-KatjaNeural",
        "Können alle mich gut hören? Ich teile jetzt meinen Bildschirm. Auf dieser Folie seht ihr "
        "den aktuellen Projektstand: Die Schnittstelle zum Lagerverwaltungssystem ist fertig, die "
        "Tests laufen aber noch. Nächste Woche haben wir einen Termin mit dem Kunden in München, "
        "und bis dahin sollten wir die offenen Punkte geklärt haben. Wer kümmert sich um die "
        "Dokumentation? Ich glaube, das wäre bei Thomas am besten aufgehoben.",
    ),
    "de_calib2": (
        "de-DE-FlorianMultilingualNeural",
        "Ehrlich gesagt bin ich mit dem Zeitplan nicht ganz zufrieden. Wenn wir die Migration auf "
        "die neue Datenbank verschieben, verlieren wir mindestens drei Wochen. Andererseits ist das "
        "Risiko geringer, wenn wir erst die Altdaten bereinigen. Vielleicht können wir einen "
        "Kompromiss finden: Wir starten mit einem kleinen Pilotbereich und entscheiden dann im Januar.",
    ),
    "en_calib1": (
        "en-GB-RyanNeural",
        "Sorry, I was on mute. What I was trying to say is that the budget for the second phase "
        "has not been approved yet, so we should be careful about committing to any dates. "
        "Can we take this offline and schedule a separate call with finance on Thursday afternoon?",
    ),
    "en_calib2": (
        "en-US-EmmaMultilingualNeural",
        "Quick update from the design team. We ran five user interviews last week, and the main "
        "feedback was that the onboarding flow feels too long. People drop off at the third screen, "
        "where we ask for company details. Our proposal is to make that step optional and move it "
        "into the settings page instead. I will share the recordings in the channel after this meeting.",
    ),
}


# Mandarin, German-class style (incl. code-switching), for language-ID / routing tests.
ZH = {
    "zh1": (
        "zh-CN-YunxiNeural",
        "好，我们先看一下第三课的生词。Ehrenamt 这个词的意思是志愿者的工作，就是不拿工资、"
        "自愿为社会做的事情。大家注意它是中性名词，所以要说 das Ehrenamt。",
    ),
    "zh2": (
        "zh-CN-XiaoxiaoNeural",
        "下面请大家打开课本第五十二页，做一下第二题。先自己读一遍课文，然后两个人一组讨论，"
        "十分钟以后我们一起对答案。有问题的话可以随时举手问我。",
    ),
    "zh3": (
        "zh-CN-YunjianNeural",
        "这个句子里动词放在最后，因为前面有 weil，这是一个从句。德语的从句和中文的语序很不一样，"
        "所以刚开始学的时候大家经常会搞错，多练习几次就好了。",
    ),
}


async def synth(voice: str, text: str) -> tuple[np.ndarray, int]:
    buf = io.BytesIO()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    buf.seek(0)
    audio, sr = sf.read(buf, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, sr


def resample(audio: np.ndarray, sr: int, target: int = 16_000) -> np.ndarray:
    if sr == target:
        return audio
    n = int(round(len(audio) * target / sr))
    x_old = np.linspace(0, 1, len(audio), endpoint=False)
    x_new = np.linspace(0, 1, n, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


async def main(out_dir: Path, which: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    refs = {}
    for lang, (voice, text) in {"calib": CALIB, "zh": ZH}.get(which, SAMPLES).items():
        audio, sr = await synth(voice, text)
        audio16 = resample(audio, sr)
        sf.write(out_dir / f"{lang}.wav", audio16, 16_000, subtype="PCM_16")
        refs[lang] = text
        print(f"{lang}: {len(audio16) / 16_000:.1f}s (source sr={sr})")
    (out_dir / "refs.json").write_text(json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "test"))
