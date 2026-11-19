"""Per-stream processing: VAD -> utterance segmentation -> STT -> caption events.

An utterance starts when the VAD fires and ends after `min_silence` of non-speech.
While it is running, the buffer is re-transcribed every `partial_interval` to show
tentative text. Captions follow sentences: as soon as a sentence end has `tail_keep`
seconds of audio after it, the caption is closed and the speech continues in a new one,
so a caption is one sentence even when nobody pauses (see SegmenterConfig).

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
from .stt import SttEngine, Transcript, Word, is_cjk, join_text, words_text
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
    # Captions follow sentences, not pauses: while someone speaks, a caption is closed as
    # soon as a sentence end has tail_keep seconds of audio after it, and whatever is
    # committed at a pause or a forced cut is cut into one caption per sentence (dialogue
    # with pauses < min_silence used to give captions of 3-4 sentences and up to 40 words;
    # film subtitles have 7 words per line, 17 at most). A sentence of fewer than
    # min_sentence words shares the caption of the next one. After max_caption seconds
    # without a sentence end the caption is closed at a comma (or at the cut itself).
    # Only words >= tail_keep before the window end count: Parakeet puts a full stop at the
    # end of any clip, so the last words of a live recognition are no evidence of a sentence
    # end (a "shorter pause after a full stop" rule split "OpenShell and BlueField" mid-phrase).
    min_sentence: int = 3
    max_caption: float = 8.0
    max_segment: float = 9.0  # forced cut length (must stay below the engine limit)
    tail_keep: float = 1.5  # s of audio left uncommitted at a forced cut
    overlap_margin: float = 0.15  # s re-heard before the last committed word's onset
    min_advance: float = 1.0  # a forced cut must advance the buffer by at least this much
    unheard: float = 1.5  # s of speech (VAD) in one stretch without words: the model skipped it, retry
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


def sentence_groups(words: list[Word], min_words: int, have: int = 0) -> tuple[list[list[Word]], list[Word]]:
    """Cut words into captions at sentence ends: (complete captions, the words left over).
    A sentence that leaves the caption below min_words words (`have`: words the caption
    already holds) waits for the next one, so "No." gets no caption of its own mid-speech."""
    groups: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        cur.append(w)
        if ends_sentence(w.text) and len(cur) + have >= min_words:
            groups.append(cur)
            cur, have = [], 0
    return groups, cur


def capitalized(text: str) -> str:
    """The text with a capital first letter (a caption that starts a new sentence)."""
    return text[:1].upper() + text[1:]


def count_words(text: str) -> int:
    """Words of a caption text; a CJK character counts as one."""
    return sum(len(t) if any(is_cjk(c) for c in t) else 1 for t in text.split())


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


def _gaps(words: list[Word], dur: float) -> list[tuple[float, float]]:
    """The stretches without words: gap i lies before words[i], the last one after all words."""
    edges = [0.0, *(t for w in words for t in (w.start, w.end)), dur]
    return list(zip(edges[::2], edges[1::2]))


def _speech_in(speech: list[bool], a: float, b: float) -> float:
    """Seconds of speech (the VAD's per-frame flags) between a and b, 0.3 s away from both."""
    frame = FRAME / SR
    i, j = int((a + 0.3) / frame), int((b - 0.3) / frame)
    return sum(speech[i:j]) * frame if j > i else 0.0


def unheard_speech(words: list[Word], speech: list[bool], dur: float) -> float:
    """Most speech inside one stretch of the buffer without recognized words."""
    return max(_speech_in(speech, a, b) for a, b in _gaps(words, dur))


def restore_heard(words: list[Word], heard: list[Word], speech: list[bool], dur: float, need: float) -> list[Word]:
    """Fill stretches where this pass left out >= `need` s of speech with the words the
    previous window had heard there (`heard`, in this buffer's time) but not yet committed."""
    if not heard:
        return words
    out: list[Word] = []
    for i, (a, b) in enumerate(_gaps(words, dur)):
        if _speech_in(speech, a, b) >= need:
            out += [h for h in heard if h.start >= a - 0.05 and h.end <= b + 0.05]
        out += words[i : i + 1]
    return out


_NOISE = np.random.default_rng(0).standard_normal(31 * SR).astype(np.float32)  # fixed: same audio, same text


def _noisy(audio: np.ndarray, snr_db: float) -> np.ndarray:
    """The audio with white noise `snr_db` below its own level."""
    rms = float(np.sqrt(np.mean(audio * audio))) if len(audio) else 0.0
    return audio + np.resize(_NOISE, len(audio)) * (rms * 10 ** (-snr_db / 20))


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
        self.sentence_cuts = 0  # captions closed at a sentence end while the speech went on
        self.retries = 0
        self.restored = 0  # words taken over from the previous window (see _words)
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
        self._speech.append(p >= cfg.end_threshold)
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
        self._speech = [False] * len(self._buf)  # per buffer frame: did the VAD hear speech
        self._heard: list[Word] = []  # words of the previous window that were not committed yet
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
        self._after_sentence = False  # this caption continues right after a sentence end
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

        robust: Parakeet occasionally returns nothing, skips the first seconds, a passage
        in the middle (two people at once) or stops seconds before the speech does - mostly
        for a window that starts mid-speech, and much more often with quantized encoders
        (bench/to_int8.py). The result flips with tiny changes of the input, so a suspect
        result (see _suspect) is retried on other views of the same audio: without its
        first 0.1 s, then with faint noise. What is still missing then
        is taken from the previous window if that one had heard it (a passage with two
        voices was recognised in one window and skipped in the next, in every view of it).
        Used for committing transcriptions only (finals / forced cuts), not partials.

        Silence in front, the retry until 0.2.0, does the opposite: silence followed by
        speech that starts mid-word made even the fp32 model lose > 40 % of the words in
        17 of 55 healthy windows, and it recovered 6 of 23 collapsed ones (trim: 11, noise: 18).
        (Known gap: with two people talking at once a few seconds can still be lost.)
        """
        words = self._recognize(audio).words
        if robust and self._suspect(words, len(audio)):
            for alt in self._other_views(audio):
                if len(alt) > len(words):
                    words = alt
                if not self._suspect(words, len(audio)):
                    break
            self.retries += 1
            n = len(words)
            words = restore_heard(words, self._heard, self._speech, len(audio) / SR, self.cfg.unheard)
            self.restored += len(words) - n
        return drop_overlap(words, self._tail, self._boundary)

    def _suspect(self, words: list[Word], n: int) -> bool:
        """Did the model skip the start, or leave out speech that the VAD heard?"""
        dur = n / SR
        return _starts_late(words, dur) or unheard_speech(words, self._speech, dur) >= self.cfg.unheard

    def _other_views(self, audio: np.ndarray):
        trim = 0.1  # well inside the pre-roll / the re-heard word before the first new one
        words = self._recognize(audio[int(trim * SR) :]).words
        yield [Word(w.text, w.start + trim, w.last + trim, w.space) for w in words]
        yield self._recognize(_noisy(audio, 12.0)).words

    def _emit(self, committed: str, tentative: str, final: bool, end: int | None = None) -> None:
        """end: stream position (samples) where the caption ends; default: now."""
        if self._after_sentence:  # the model saw these words mid-window and may not capitalize them
            committed, tentative = (capitalized(committed), tentative) if committed else ("", capitalized(tentative))
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
        audio = self._audio()
        words = self._words(audio)
        self._last_update = self._pos
        k = self._sentence_ready(words, len(audio))
        if k is None:
            self._emit(self._committed, words_text(words), final=False)
            return
        self.sentence_cuts += 1
        rest = words[k:]
        self._cut(audio, words[:k], rest, rest[0].start if rest else words[k - 1].end + 0.2)

    def _sentence_ready(self, words: list[Word], n: int) -> int | None:
        """Number of leading words that can be committed right now: up to the last sentence
        end (a clause end once the caption is max_caption seconds old) that has tail_keep
        seconds of audio after it, lies far enough into the buffer for a cut, and closes a
        caption. Not on a recognition that left speech out."""
        cfg = self.cfg
        limit, first = n / SR - cfg.tail_keep, cfg.min_advance + cfg.overlap_margin
        ok = [i for i, w in enumerate(words) if first <= w.start and w.end <= limit]
        k = next((i + 1 for i in reversed(ok) if ends_sentence(words[i].text)), None)
        if k is not None and not sentence_groups(words[:k], cfg.min_sentence, count_words(self._committed))[0]:
            k = None  # only sentences too short for a caption of their own so far
        if k is None and (self._pos - self._utt_start) >= cfg.max_caption * SR:
            k = next((i + 1 for i in reversed(ok) if words[i].text.rstrip().rstrip(_CLOSERS)[-1:] in _CLAUSE_END), None)
        return None if k is None or self._suspect(words, n) else k

    def _caption_done(self, text: str) -> None:
        if text:
            self._prev_lang = self._lang or "eu"
            self.zh_utterances += self._lang == "zh"

    def _finalize(self) -> None:
        cfg = self.cfg
        trailing = max(0, self._silence * FRAME - int(cfg.post_pad * SR))
        audio = self._audio()[: self._buf_len - trailing]
        if self._speech_frames * FRAME >= cfg.min_speech * SR or self._committed:
            self._split_caption(self._words(audio, robust=True), len(audio) / SR, closing=True)
        full = self._committed
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
        self.forced_cuts += 1
        self._cut(audio, commit, rest, next_onset)

    def _cut(self, audio: np.ndarray, commit: list[Word], rest: list[Word], next_onset: float) -> None:
        """Commit `commit` and restart the buffer just before its last word: that word is
        heard again whole, so the overlap can be matched by text. next_onset: where the first
        uncommitted word starts."""
        cfg = self.cfg
        dur = len(audio) / SR
        cut_t = min(commit[-1].start - cfg.overlap_margin, next_onset - 0.3) if commit else next_onset - 0.3
        cut_n = int(np.clip(cut_t * SR, cfg.min_advance * SR, len(audio) - 0.5 * SR))
        self._split_caption(commit, rest[0].start if rest else next_onset)
        self._tail = (self._tail + [_norm(w.text) for w in commit])[-3:]
        self._boundary = next_onset - cut_n / SR
        self._buf = [audio[cut_n:]]
        self._buf_len = len(audio) - cut_n
        self._speech = self._speech[round(cut_n / FRAME) :]
        # not its last half second: a window's final word is often a clipped one
        self._heard = [Word(w.text, w.start - cut_n / SR, w.last - cut_n / SR, w.space) for w in rest if w.end <= dur - 0.5]
        self._last_update = self._pos
        if self._committed or rest:  # nothing to show yet for a caption that has no words
            self._emit(self._committed, words_text(rest), final=False)

    def _split_caption(self, commit: list[Word], next_start: float, closing: bool = False) -> None:
        """Commit words. Every sentence in them that completes a caption (sentence_groups)
        closes one: it goes out as final and the speech continues in a new caption, with the
        same language decision. After max_caption seconds without a sentence end the caption
        is closed at a clause end or at the end of `commit`. What is left stays in
        self._committed. closing: the utterance ends here and the caller sends the last
        caption, so the last sentence is left for it. Word times are relative to the buffer;
        next_start: where the first word after `commit` starts."""
        cfg = self.cfg
        buf_start = self._pos - self._buf_len  # stream position of the buffer's first sample
        groups, carry = sentence_groups(commit, cfg.min_sentence, count_words(self._committed))
        if closing and groups and not carry:
            carry = groups.pop()
        elif not closing and not groups and carry and (self._pos - self._utt_start) >= cfg.max_caption * SR:
            k = sentence_split(carry, clause=True) or len(carry)
            groups, carry = [carry[:k]], carry[k:]
        rest = commit
        for g in groups:
            rest = rest[len(g) :]
            done = _join(self._committed, words_text(g))
            self._emit(done, "", final=True, end=buf_start + int(g[-1].end * SR))
            self._caption_done(done)
            first = rest[0].start if rest else next_start
            self._utt_id = next(self._utt_ids)
            self._utt_start = buf_start + int(max(0.0, first - 0.1) * SR)
            self._committed = ""
            self._after_sentence = ends_sentence(g[-1].text)
        self._committed = _join(self._committed, words_text(carry))

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
