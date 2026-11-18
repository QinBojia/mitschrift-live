"""SenseVoice-Small (FunAudioLLM, int8 ONNX) on CPU: Chinese ASR + spoken-language ID.

Parakeet covers German/English but has no Chinese: it turns Chinese speech into
confident English-like nonsense. SenseVoice transcribes Chinese well and its first
output frame is a language tag, so one call gives both the language and the text.

Pipeline (matches FunASR / sherpa-onnx): 80-dim Kaldi fbank (hamming, 25/10 ms, on
int16-scaled samples) -> LFR stacking (7 frames, shift 6 = 60 ms) -> CMVN -> CTC.
"""

from __future__ import annotations

import collections
import statistics
import threading
import time
from pathlib import Path

import kaldi_native_fbank as knf
import numpy as np
import onnxruntime as rt

from . import SAMPLE_RATE
from .stt import Transcript, Word, is_cjk

LID_LANGS = ("zh", "en", "yue", "ja", "ko", "nospeech")
_PUNCT = set("，。、？！：；,.?!:;…")


class SenseVoice:
    def __init__(self, model_dir: Path, threads: int = 4):
        so = rt.SessionOptions()
        so.intra_op_num_threads = threads
        so.add_session_config_entry("session.intra_op.allow_spinning", "0")
        so.add_session_config_entry("session.inter_op.allow_spinning", "0")
        self._sess = rt.InferenceSession(str(model_dir / "model.int8.onnx"), so, providers=["CPUExecutionProvider"])
        md = self._sess.get_modelmeta().custom_metadata_map
        self._lfr_m, self._lfr_n = int(md["lfr_window_size"]), int(md["lfr_window_shift"])
        self.frame_s = self._lfr_n * 0.01
        self._neg_mean = np.array(md["neg_mean"].split(","), dtype=np.float32)
        self._inv_std = np.array(md["inv_stddev"].split(","), dtype=np.float32)
        self._lang_input = {k[5:]: int(v) for k, v in md.items() if k.startswith("lang_")}
        self._with_itn = int(md["with_itn"])
        self._tokens = [ln.rsplit(" ", 1)[0] for ln in (model_dir / "tokens.txt").read_text(encoding="utf-8").splitlines()]
        tag_id = {t: i for i, t in enumerate(self._tokens) if t.startswith("<|")}
        self._lid_ids = {lang: tag_id[f"<|{lang}|>"] for lang in LID_LANGS if f"<|{lang}|>" in tag_id}
        self._lock = threading.Lock()
        self.calls = 0  # recognize() calls so far (for the speed log)
        self.timings: collections.deque[float] = collections.deque(maxlen=200)

    def stats(self) -> tuple[float, float] | None:
        """(mean, p95) recognize time in ms over recent calls."""
        ts = sorted(self.timings.copy())
        if not ts:
            return None
        return statistics.mean(ts), ts[int(0.95 * (len(ts) - 1))]

    def _features(self, audio: np.ndarray) -> np.ndarray:
        opts = knf.FbankOptions()
        opts.frame_opts.dither = 0
        opts.frame_opts.snip_edges = True
        opts.frame_opts.window_type = "hamming"
        opts.frame_opts.samp_freq = SAMPLE_RATE
        opts.mel_opts.num_bins = 80
        fb = knf.OnlineFbank(opts)
        fb.accept_waveform(SAMPLE_RATE, (audio * 32768).astype(np.float32).tolist())
        fb.input_finished()
        feats = np.array([fb.get_frame(i) for i in range(fb.num_frames_ready)], dtype=np.float32)
        # LFR: stack lfr_m frames every lfr_n frames, left-padded with copies of the first frame
        m, n = self._lfr_m, self._lfr_n
        feats = np.vstack([np.repeat(feats[:1], (m - 1) // 2, axis=0), feats])
        t_lfr = int(np.ceil((len(feats) - (m - 1) // 2) / n))
        need = (t_lfr - 1) * n + m
        if need > len(feats):
            feats = np.vstack([feats, np.repeat(feats[-1:], need - len(feats), axis=0)])
        idx = np.arange(t_lfr)[:, None] * n + np.arange(m)[None, :]
        lfr = feats[idx].reshape(t_lfr, -1)
        return (lfr + self._neg_mean) * self._inv_std

    def recognize(self, audio: np.ndarray, language: str = "auto") -> tuple[Transcript, dict[str, float]]:
        """Returns the transcript (with lang = most likely language) and LID probabilities."""
        if len(audio) < int(0.3 * SAMPLE_RATE):
            return Transcript("", [], None), {}
        t0 = time.perf_counter()
        x = self._features(audio)[None]
        feeds = {
            "x": x,
            "x_length": np.array([x.shape[1]], dtype=np.int32),
            "language": np.array([self._lang_input.get(language, 0)], dtype=np.int32),
            "text_norm": np.array([self._with_itn], dtype=np.int32),
        }
        with self._lock:
            logits = self._sess.run(["logits"], feeds)[0][0]
            self.timings.append((time.perf_counter() - t0) * 1000)
            self.calls += 1
        lid_logits = np.array([logits[0, i] for i in self._lid_ids.values()])
        p = np.exp(lid_logits - lid_logits.max())
        probs = dict(zip(self._lid_ids, (p / p.sum()).tolist(), strict=True))
        lang = max(probs, key=probs.get)

        ids = logits[4:].argmax(axis=-1)  # frames 0-3 are language / emotion / event / ITN tags
        words: list[Word] = []
        prev = -1
        for f, tid in enumerate(ids):
            if tid != prev and tid != 0:
                self._add_token(words, self._tokens[tid], f * self.frame_s)
            prev = tid
        text = "".join((" " if i and w.space else "") + w.text for i, w in enumerate(words))
        return Transcript(text.strip(), words, lang), probs

    @staticmethod
    def _add_token(words: list[Word], tok: str, t: float) -> None:
        if tok.startswith("<|"):
            return
        if tok.startswith("▁"):  # start of a Latin word
            if len(tok) > 1:
                words.append(Word(tok[1:], t, t, space=True))
            return
        if tok in _PUNCT:
            if words:
                words[-1].text += tok
                words[-1].last = t
            return
        prev = words[-1].text[-1] if words else ""
        if is_cjk(tok[0]) or not prev or is_cjk(prev) or prev in _PUNCT:
            # a CJK character, or a Latin/digit token right after CJK text: a new word
            words.append(Word(tok, t, t, space=False))
        else:  # sub-word continuation of a Latin word
            words[-1].text += tok
            words[-1].last = t
