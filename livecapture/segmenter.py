"""Per-stream processing: VAD -> utterance segmentation -> STT -> caption events.

An utterance starts when the VAD fires and ends after `min_silence` of non-speech.
While it is running, the buffer is re-transcribed every `partial_interval` to show
tentative text.

If an utterance grows past `max_segment` (the NPU can only take 10 s), the words up
to a natural boundary ~`tail_keep` before the end are committed and the buffer
restarts just before the *last committed* word. Parakeet's token timestamps are coarse
(80 ms) and drift 0.1-0.3 s between passes, so cutting exactly at a word start clips
it, and timing alone cannot tell a re-heard word from the next one. Re-hearing one
whole committed word instead makes the overlap recognisable by text: the leading
words that repeat the committed tail are dropped (timing is only a last resort for
clipped fragments).
"""

from __future__ import annotations

import itertools
import math
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from . import SAMPLE_RATE as SR
from .sensevoice import SenseVoice
from .stt import SttEngine, Transcript, Word, join_text, words_text
from .vad import FRAME, SileroVad


@dataclass
class Caption:
    stream: str  # "other" (system audio) | "me" (microphone)
    utt_id: int
    start: float  # wall-clock epoch seconds
    end: float
    committed: str  # stable text, will not change
    tentative: str  # may still change on the next update
    final: bool  # utterance finished
    lang: str | None = None  # "zh" (SenseVoice) or None (Parakeet: de/en/other European)
    translation: str | None = None  # filled in by translate.Translator ("…" while pending)

    @property
    def text(self) -> str:
        return _join(self.committed, self.tentative)


@dataclass
class SegmenterConfig:
    start_threshold: float = 0.5
    end_threshold: float = 0.35
    min_silence: float = 0.5  # s of non-speech that ends an utterance
    pre_roll: float = 0.3  # s of audio kept before speech onset
    post_pad: float = 0.2  # s of trailing silence kept in the final segment
    min_speech: float = 0.25  # shorter blips are dropped
    partial_interval: float = 0.5
    min_partial: float = 0.6  # first partial after this much audio
    # Slow CPUs: stretch the partial interval to >= adaptive_factor x the recent mean
    # recognition time (capped), so live updates never pile up; finals are unaffected.
    adaptive_factor: float = 4.0
    max_partial_interval: float = 3.0
    # Speech without pauses: a caption is closed at the last sentence end at each forced
    # cut, and after max_caption seconds without one, at a comma (or the cut itself). Only
    # words >= tail_keep before the window end count: Parakeet puts a full stop at the end
    # of any clip, so the last words of a live recognition are no evidence of a sentence end
    # (a "shorter pause after a full stop" rule split "OpenShell and BlueField" mid-phrase).
    max_caption: float = 25.0
    max_segment: float = 9.0  # forced cut length (must stay below the engine limit)
    tail_keep: float = 1.5  # s of audio left uncommitted at a forced cut
    overlap_margin: float = 0.15  # s re-heard before the last committed word's onset
    min_advance: float = 1.0  # a forced cut must advance the buffer by at least this much
    # Chinese routing (SenseVoice language ID; see bench/lid_eval.py)
    zh_threshold: float = 0.5  # P(zh)+P(yue) needed to route an utterance to SenseVoice
    zh_prior: float = 0.15  # threshold shifts by this toward the previous utterance's language
    lid_lock: float = 2.5  # s of audio after which an utterance's language is fixed


def _join(a: str, b: str) -> str:
    return join_text(a.strip(), b.strip())


def _norm(text: str) -> str:
    return re.sub(r"[^\w]", "", text.lower())


_SENTENCE_END = ".?!。？！…"
_CLAUSE_END = ",;:，；：、"
_CLOSERS = "\"'”’)）」』»"
# a final "." after these is not a sentence end (German and English)
_ABBREV = {"dr", "mr", "mrs", "ms", "prof", "st", "nr", "ca", "bzw", "usw", "etc", "vs", "z.b", "e.g", "i.e", "u.a", "d.h"}


def ends_sentence(text: str) -> bool:
    t = text.rstrip().rstrip(_CLOSERS)
    if not t or t[-1] not in _SENTENCE_END:
        return False
    if t[-1] == "." and not t.endswith("..."):
        last = t.split()[-1].rstrip(".").lower()
        if last in _ABBREV or last.isdigit() or (len(last) == 1 and last.isalpha()):
            return False  # "Dr.", "z.B.", "1." (German ordinal), "A."
    return True


def sentence_split(words: list[Word], clause: bool = False) -> int | None:
    """Number of leading words up to the last one that ends a sentence (or, with clause=True,
    a sentence or clause), or None."""
    for i in range(len(words) - 1, -1, -1):
        text = words[i].text.rstrip().rstrip(_CLOSERS)
        if ends_sentence(words[i].text) or (clause and text[-1:] in _CLAUSE_END):
            return i + 1
    return None


def rms_db(x: np.ndarray) -> float:
    if not len(x):
        return -100.0
    return 20 * math.log10(max(float(np.sqrt(np.mean(x * x))), 1e-5))


def choose_boundary(words: list[Word], limit: float, min_prev_start: float) -> int | None:
    """Index k of the first uncommitted word (words[:k] get committed), with
    words[k].start <= limit and words[k-1].start >= min_prev_start. Prefers the widest
    inter-word gap (bonus after punctuation) within the 3 s before `limit`."""
    cands = [
        k for k in range(1, len(words)) if words[k].start <= limit and words[k - 1].start >= min_prev_start
    ]
    if not cands:
        return None
    recent = [k for k in cands if words[k].start >= limit - 3.0] or cands

    def score(k: int) -> tuple[float, int]:
        gap = words[k].start - words[k - 1].last
        punct = 0.3 if words[k - 1].text[-1:] in ".,!?;:" else 0.0
        return gap + punct, k

    return max(recent, key=score)


def _starts_late(words: list[Word], dur: float) -> bool:
    """Speech is expected right at the buffer start (pre-roll or overlap); a first word
    more than ~1.3 s in (or none at all) on a long buffer means the model skipped audio."""
    return dur >= 1.5 and (not words or words[0].start > 1.3)


def drop_overlap(words: list[Word], tail: list[str], boundary: float) -> list[Word]:
    """Remove the leading words of a re-transcribed buffer that repeat committed text.

    tail: normalized last committed words. boundary: time (s, buffer-relative) where
    the first uncommitted word was heard. Looks for leading words that repeat the end
    of `tail` (the first may be a clipped fragment, e.g. "gleich" for "Vergleich"),
    possibly preceded by up to two junk words the model made out of the clipped
    sound in the overlap. Without a text match, drops leading words that start
    clearly before the boundary.
    """
    if not words or not tail:
        return words
    lead = [w for w in words[:6] if w.start < boundary + 0.35]
    norm = [_norm(w.text) for w in lead]
    best: tuple[int, int] | None = None  # (matched words j, -junk i)
    for i in range(min(3, len(lead))):
        if i > 0 and lead[i - 1].start >= boundary - 0.05:
            break  # junk must lie inside the overlap region
        for j in range(min(len(lead) - i, len(tail)), 0, -1):
            first = norm[i]
            first_ok = first == tail[-j] or (len(first) >= 2 and tail[-j].endswith(first))
            if first_ok and norm[i + 1 : i + j] == tail[len(tail) - j + 1 :]:
                if best is None or (j, -i) > best:
                    best = (j, -i)
                break
    if best:
        return words[best[0] - best[1] :]
    n = 0
    while n < len(words) and words[n].start < boundary - 0.2:
        n += 1
    return words[n:]


class StreamProcessor(threading.Thread):
    def __init__(
        self,
        stream: str,
        engine: SttEngine,
        vad: SileroVad,
        cfg: SegmenterConfig,
        on_caption: Callable[[Caption], None],
        *,
        live: bool = True,
        on_level: Callable[[str, float], None] | None = None,
        on_audio: Callable[[str, np.ndarray], None] | None = None,
        sensevoice: SenseVoice | None = None,
    ):
        super().__init__(daemon=True, name=f"proc-{stream}")
        self.stream, self.engine, self.vad, self.cfg = stream, engine, vad, cfg
        self.on_caption, self.on_level, self.on_audio = on_caption, on_level, on_audio
        self.live = live
        self.sv = sensevoice  # None: Parakeet only, no Chinese
        self._prev_lang: str | None = None  # previous utterance: "zh", "eu" (Parakeet) or None
        self.zh_utterances = 0
        self._q: queue.Queue[tuple[float, np.ndarray]] = queue.Queue()
        self._stop_evt = threading.Event()
        self._pending = np.zeros(0, dtype=np.float32)
        self._pos = 0  # samples pushed through the VAD
        self._received = 0  # samples received (incl. synthetic silence)
        self._t0_mono: float | None = None
        self._t0_epoch = time.time()
        self._last_data = 0.0
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, round(cfg.pre_roll * SR / FRAME)))
        self._utt_ids = itertools.count(1)
        self._in_speech = False
        self.forced_cuts = 0
        self.retries = 0
        self.lag = 0.0  # s between real time and processed audio (live only)

    # ---- producer side -------------------------------------------------
    def feed(self, block: np.ndarray) -> None:
        """Called from the capture thread with 16 kHz mono float32 audio."""
        self._q.put((time.monotonic(), block))

    def stop(self) -> None:
        self._stop_evt.set()

    # ---- consumer thread -----------------------------------------------
    def run(self) -> None:
        while True:
            try:
                arrived, block = self._q.get(timeout=0.05)
            except queue.Empty:
                if self._stop_evt.is_set():
                    break
                if self.live and self._t0_mono is not None:
                    self._fill_gap()
                continue
            if self._t0_mono is None:
                self._t0_mono = arrived - len(block) / SR
                self._t0_epoch = time.time() - (time.monotonic() - self._t0_mono)
            self._last_data = time.monotonic()
            self._ingest(block)
            if self.live:
                self.lag = max(0.0, time.monotonic() - self._t0_mono - self._pos / SR)
        if self._in_speech:
            self._finalize()

    def _fill_gap(self) -> None:
        """WASAPI loopback delivers nothing while no app is playing; keep time moving with silence."""
        now = time.monotonic()
        if now - self._last_data < 0.25:
            return
        missing = int((now - self._t0_mono) * SR) - self._received
        if missing > int(0.1 * SR):
            self._ingest(np.zeros(missing, dtype=np.float32))

    def _ingest(self, block: np.ndarray) -> None:
        self._received += len(block)
        if self.on_audio:
            self.on_audio(self.stream, block)
        if self.on_level:
            self.on_level(self.stream, rms_db(block))
        self._pending = np.concatenate([self._pending, block])
        n = len(self._pending) // FRAME
        for i in range(n):
            self._on_frame(self._pending[i * FRAME : (i + 1) * FRAME])
        self._pending = self._pending[n * FRAME :]

    def _on_frame(self, frame: np.ndarray) -> None:
        cfg = self.cfg
        p = self.vad(frame)
        self._pos += FRAME
        if not self._in_speech:
            self._preroll.append(frame)
            if p >= cfg.start_threshold:
                self._start_utterance()
            return

        self._buf.append(frame)
        self._buf_len += FRAME
        if p >= cfg.end_threshold:
            self._silence = 0
            if p >= cfg.start_threshold:
                self._speech_frames += 1
        else:
            self._silence += 1

        if self._silence * FRAME >= cfg.min_silence * SR:
            self._finalize()
        elif self._buf_len >= cfg.max_segment * SR:
            self._forced_cut()
        elif (
            self._pos - self._last_update >= self._partial_interval() * SR
            and self._buf_len >= cfg.min_partial * SR
            and self._q.qsize() < 10  # skip partials while catching up on a backlog
        ):
            self._partial()

    def _partial_interval(self) -> float:
        """Seconds between live re-transcriptions, widened when recognition is slow."""
        cfg = self.cfg
        stats = self.engine.stats() if cfg.adaptive_factor else None
        if not stats:
            return cfg.partial_interval
        return min(cfg.max_partial_interval, max(cfg.partial_interval, cfg.adaptive_factor * stats[0] / 1000))

    # ---- utterance lifecycle ---------------------------------------------
    def _start_utterance(self) -> None:
        self._in_speech = True
        self._buf = list(self._preroll)
        self._buf_len = sum(len(f) for f in self._buf)
        self._preroll.clear()
        self._utt_id = next(self._utt_ids)
        self._utt_start = self._pos - self._buf_len
        self._committed = ""
        self._tail: list[str] = []  # normalized last committed words (for overlap removal)
        self._boundary = 0.0  # buffer-relative onset of the first uncommitted word
        self._silence = 0
        self._speech_frames = 1
        self._last_update = self._pos
        self._lang: str | None = None  # "zh" -> SenseVoice, None -> Parakeet
        self._lang_locked = self.sv is None

    def _recognize(self, audio: np.ndarray) -> Transcript:
        """Route to SenseVoice (Chinese) or Parakeet (German/English/...).

        Until the utterance has lid_lock seconds of audio, every call re-runs language
        ID (SenseVoice gives the language and the Chinese text in one ~40-85 ms CPU call).
        Short or ambiguous starts lean toward the previous utterance's language.
        """
        if not self._lang_locked:
            tr, probs = self.sv.recognize(audio)
            p_zh = probs.get("zh", 0.0) + probs.get("yue", 0.0)
            prior = {"zh": -self.cfg.zh_prior, "eu": self.cfg.zh_prior}.get(self._prev_lang, 0.0)
            self._lang = "zh" if p_zh >= self.cfg.zh_threshold + prior else None
            if len(audio) >= self.cfg.lid_lock * SR:
                self._lang_locked = True
            if self._lang == "zh":
                tr.lang = "zh"
                return tr
        elif self._lang == "zh":
            tr, _ = self.sv.recognize(audio)
            tr.lang = "zh"
            return tr
        return self.engine.transcribe(audio)

    def _audio(self) -> np.ndarray:
        return np.concatenate(self._buf) if self._buf else np.zeros(0, dtype=np.float32)

    def _words(self, audio: np.ndarray, robust: bool = False) -> list[Word]:
        """Transcribe the buffer and remove words re-heard from the previous cut.

        robust: Parakeet occasionally returns nothing (or skips the first seconds) for a
        window that starts mid-speech; the result flips with tiny shifts of the window.
        When the text starts implausibly late, retry with a little leading silence.
        Used for committing transcriptions only (finals / forced cuts), not partials.
        (Known gap: with two people talking at once the int8 models can drop a few seconds
        that no re-try recovers - recognising the skipped head alone gave nothing either.)
        """
        words = self._recognize(audio).words
        dur = len(audio) / SR
        if robust and _starts_late(words, dur):
            for pad in (0.2, 0.1):
                padded = np.concatenate([np.zeros(int(pad * SR), dtype=np.float32), audio])
                alt = [Word(w.text, w.start - pad, w.last - pad, w.space) for w in self._recognize(padded).words]
                if len(alt) > len(words):
                    words = alt
                if not _starts_late(alt, dur):
                    break
            self.retries += 1
        return drop_overlap(words, self._tail, self._boundary)

    def _emit(self, committed: str, tentative: str, final: bool, end: int | None = None) -> None:
        """end: stream position (samples) where the caption ends; default: now."""
        self.on_caption(
            Caption(
                stream=self.stream,
                utt_id=self._utt_id,
                start=self._t0_epoch + self._utt_start / SR,
                end=self._t0_epoch + (self._pos if end is None else end) / SR,
                committed=committed,
                tentative=tentative,
                final=final,
                lang=self._lang,
            )
        )

    def _partial(self) -> None:
        words = self._words(self._audio())
        self._last_update = self._pos
        self._emit(self._committed, words_text(words), final=False)

    def _caption_done(self, text: str) -> None:
        if text:
            self._prev_lang = self._lang or "eu"
            self.zh_utterances += self._lang == "zh"

    def _finalize(self) -> None:
        cfg = self.cfg
        trailing = max(0, self._silence * FRAME - int(cfg.post_pad * SR))
        audio = self._audio()[: self._buf_len - trailing]
        text = ""
        if self._speech_frames * FRAME >= cfg.min_speech * SR or self._committed:
            text = words_text(self._words(audio, robust=True))
        full = _join(self._committed, text)
        self._emit(full, "", final=True)
        self._caption_done(full)
        self._in_speech = False
        self._buf, self._buf_len = [], 0

    def _forced_cut(self) -> None:
        cfg = self.cfg
        audio = self._audio()
        dur = len(audio) / SR
        words = self._words(audio, robust=True)  # overlap with the previous cut already removed
        margin = cfg.overlap_margin
        k = choose_boundary(words, dur - cfg.tail_keep, cfg.min_advance + margin)
        if k is not None:
            commit, rest = words[:k], words[k:]
            next_onset = words[k].start
        else:  # no usable word boundary: split at the quietest point
            q = self._quietest(audio, cfg.min_advance + 0.3, max(cfg.min_advance + 0.8, dur - cfg.tail_keep))
            commit = [w for w in words if w.end <= q]
            rest = words[len(commit) :]
            next_onset = q
        # re-hear the whole last committed word so the overlap can be matched by text
        cut_t = min(commit[-1].start - margin, next_onset - 0.3) if commit else next_onset - 0.3
        cut_n = int(np.clip(cut_t * SR, cfg.min_advance * SR, len(audio) - 0.5 * SR))
        self.forced_cuts += 1
        self._split_caption(commit, rest[0].start if rest else next_onset)
        self._tail = (self._tail + [_norm(w.text) for w in commit])[-3:]
        self._boundary = next_onset - cut_n / SR
        self._buf = [audio[cut_n:]]
        self._buf_len = len(audio) - cut_n
        self._last_update = self._pos
        self._emit(self._committed, words_text(rest), final=False)

    def _split_caption(self, commit: list[Word], next_start: float) -> None:
        """Commit words at a forced cut. Speech that runs on without a pause is still cut
        into captions: everything up to the last sentence end in `commit` becomes a final
        caption and the rest continues as a new one (after max_caption seconds without a
        sentence end, at a clause end or the cut). Word times are relative to the buffer."""
        cfg = self.cfg
        buf_start = self._pos - self._buf_len  # stream position of the buffer's first sample
        k = sentence_split(commit) if commit else None
        if k is None and commit and (self._pos - self._utt_start) >= cfg.max_caption * SR:
            k = sentence_split(commit, clause=True) or len(commit)
        if k is None:
            self._committed = _join(self._committed, words_text(commit))
            return
        head, carry = commit[:k], commit[k:]
        done = _join(self._committed, words_text(head))
        self._emit(done, "", final=True, end=buf_start + int(head[-1].end * SR))
        self._caption_done(done)
        # the same speaker goes on: new caption, same language decision
        first = carry[0].start if carry else next_start
        self._utt_id = next(self._utt_ids)
        self._utt_start = buf_start + int(max(0.0, first - 0.1) * SR)
        self._committed = words_text(carry)

    @staticmethod
    def _quietest(audio: np.ndarray, a: float, b: float) -> float:
        hop = int(0.02 * SR)
        lo, hi = int(a * SR), max(int(a * SR) + hop, int(b * SR))
        seg = audio[lo:hi]
        n = len(seg) // hop
        if n == 0:
            return b
        energy = (seg[: n * hop].reshape(n, hop) ** 2).mean(axis=1)
        return (lo + int(np.argmin(energy)) * hop + hop / 2) / SR
