"""Recorder: MP3 per stream with exactly the written samples, no file for an unused stream."""

import json

import numpy as np
import soundfile as sf

from livecapture import SAMPLE_RATE
from livecapture.recorder import Recorder
from livecapture.segmenter import Caption


def test_records_mp3_and_captions(tmp_path):
    rec = Recorder(tmp_path, ["other", "me"])
    t = np.arange(SAMPLE_RATE * 3) / SAMPLE_RATE
    tone = (0.2 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    for i in range(0, len(tone), 512):  # blocks, as the audio threads deliver them
        rec.on_audio("other", tone[i:i + 512])
    rec.on_caption(Caption(stream="other", utt_id=1, start=0.0, end=1.0, committed="Guten Tag.", tentative="",
                           final=True))
    rec.close()
    rec.on_audio("other", tone[:512])  # late write after close: ignored

    files = sorted(p.name for p in rec.dir.iterdir())
    assert files == ["captions.jsonl", "meta.json", "other.mp3"]  # "me" never got audio
    back, sr = sf.read(rec.dir / "other.mp3", dtype="float32")
    assert sr == SAMPLE_RATE and len(back) == len(tone)
    assert sf.info(rec.dir / "other.mp3").format == "MP3"
    mid = slice(SAMPLE_RATE, 2 * SAMPLE_RATE)
    assert np.corrcoef(tone[mid], back[mid])[0, 1] > 0.99  # same signal, no time shift
    caption = json.loads((rec.dir / "captions.jsonl").read_text(encoding="utf-8"))
    assert caption["committed"] == "Guten Tag."


def test_falls_back_to_wav_without_mp3(tmp_path, monkeypatch):
    real = sf.SoundFile

    def old_soundfile(*args, **kw):  # soundfile < 0.13 doesn't know compression_level
        if "compression_level" in kw:
            raise TypeError("unexpected keyword argument 'compression_level'")
        return real(*args, **kw)

    monkeypatch.setattr(sf, "SoundFile", old_soundfile)
    rec = Recorder(tmp_path, ["other"])
    rec.on_audio("other", np.zeros(1600, "float32"))
    rec.close()
    assert sorted(p.name for p in rec.dir.iterdir()) == ["captions.jsonl", "meta.json", "other.wav"]
    assert sf.info(rec.dir / "other.wav").frames == 1600
