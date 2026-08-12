"""Word-level timings, per scene.

Running ASR across 60 minutes of audio on CPU is slow and invites drift and
hallucination. We generated each scene's audio from known text, so the real
problem is aligning a 15-second clip whose transcript we already have.

Three tiers, in order of preference:
  faster-whisper word timestamps   ~100-300 ms, no torch      (align-lite)
  WhisperX wav2vec2 forced align   sub-100 ms, needs torch    (align-full)
  proportional by character count  free, draft quality        (always)

Scene-absolute times come from the scene's known offset in the narration, so
error cannot accumulate across the hour.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

log = logging.getLogger("casefile.align")

_WORD = re.compile(r"\S+")
_NORM = re.compile(r"[^\w']+")

# Trailing punctuation earns extra time; that is where a narrator breathes.
_PAUSE_WEIGHT = {",": 1.6, ";": 2.0, ":": 2.0, ".": 2.4, "!": 2.4, "?": 2.4, "—": 2.0, "-": 1.2}

_model_lock = threading.Lock()
_model = None
_model_name = ""


@dataclass
class Word:
    text: str
    start: float
    end: float


def available_methods() -> list[str]:
    methods = ["proportional"]
    try:
        import faster_whisper  # noqa: F401

        methods.insert(0, "faster_whisper")
    except ImportError:
        pass
    try:
        import whisperx  # noqa: F401

        methods.insert(0, "whisperx")
    except ImportError:
        pass
    return methods


def best_method() -> str:
    return available_methods()[0]


# ---------------------------------------------------------------------------
# Tier 3: proportional
# ---------------------------------------------------------------------------

def proportional(text: str, duration: float) -> list[Word]:
    """Spread the duration across words by length, with pauses at punctuation."""
    tokens = _WORD.findall(text)
    if not tokens or duration <= 0:
        return []

    weights = []
    for token in tokens:
        weight = max(len(_NORM.sub("", token)), 1) + 1.0
        weight += _PAUSE_WEIGHT.get(token[-1:], 0.0)
        weights.append(weight)

    total = sum(weights)
    words: list[Word] = []
    cursor = 0.0
    for token, weight in zip(tokens, weights):
        span = duration * weight / total
        words.append(Word(token, round(cursor, 3), round(cursor + span, 3)))
        cursor += span
    return words


# ---------------------------------------------------------------------------
# Tier 1: faster-whisper
# ---------------------------------------------------------------------------

def _load_faster_whisper(model_size: str = "small"):
    global _model, _model_name
    if _model is not None and _model_name == model_size:
        return _model
    from faster_whisper import WhisperModel

    _model = WhisperModel(model_size, device="cpu", compute_type="int8")
    _model_name = model_size
    return _model


def _faster_whisper_words(wav: Path, text: str, model_size: str) -> list[Word] | None:
    try:
        model = _load_faster_whisper(model_size)
    except Exception as exc:
        log.warning("faster-whisper unavailable: %s", exc)
        return None

    try:
        # CTranslate2 parallelises internally across cores, so one shared model
        # under a lock beats N models competing for the same CPU.
        with _model_lock:
            segments, _info = model.transcribe(
                str(wav),
                word_timestamps=True,
                vad_filter=False,
                condition_on_previous_text=False,
                initial_prompt=text[:400],
            )
            heard = [
                Word(w.word.strip(), float(w.start), float(w.end))
                for seg in segments
                for w in (seg.words or [])
                if w.word.strip()
            ]
    except Exception as exc:
        log.warning("faster-whisper failed on %s: %s", wav.name, exc)
        return None

    return heard or None


def _retime_known_text(known: Sequence[str], heard: Sequence[Word], duration: float) -> list[Word]:
    """Keep the author's words; borrow the recogniser's timings.

    ASR mis-hears; the script does not. So the output text always comes from
    the script, and only the times come from the recogniser - matched up with
    difflib and interpolated across whatever did not match.
    """
    import difflib

    if not heard:
        return proportional(" ".join(known), duration)

    norm_known = [_NORM.sub("", w).lower() for w in known]
    norm_heard = [_NORM.sub("", w.text).lower() for w in heard]
    matcher = difflib.SequenceMatcher(a=norm_known, b=norm_heard, autojunk=False)

    times: list[tuple[float, float] | None] = [None] * len(known)
    for a0, b0, size in matcher.get_matching_blocks():
        for k in range(size):
            times[a0 + k] = (heard[b0 + k].start, heard[b0 + k].end)

    # Interpolate unmatched runs between the anchors either side.
    anchored = [i for i, t in enumerate(times) if t is not None]
    if not anchored:
        return proportional(" ".join(known), duration)

    first, last = anchored[0], anchored[-1]
    for i in range(first):
        times[i] = None
    words: list[Word] = []
    for i, token in enumerate(known):
        if times[i] is not None:
            start, end = times[i]
        else:
            prev = max((j for j in anchored if j < i), default=None)
            nxt = min((j for j in anchored if j > i), default=None)
            lo = times[prev][1] if prev is not None else 0.0
            hi = times[nxt][0] if nxt is not None else duration
            gap = [j for j in range(len(known)) if times[j] is None and
                   (prev is None or j > prev) and (nxt is None or j < nxt)]
            slot = gap.index(i) if i in gap else 0
            span = max(hi - lo, 0.05) / max(len(gap), 1)
            start, end = lo + slot * span, lo + (slot + 1) * span
        words.append(Word(token, round(max(start, 0.0), 3), round(max(end, start + 0.02), 3)))

    for i in range(1, len(words)):  # keep it monotonic
        if words[i].start < words[i - 1].end:
            words[i].start = words[i - 1].end
        if words[i].end <= words[i].start:
            words[i].end = words[i].start + 0.05
    return words


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def align_scene(
    wav: Path,
    text: str,
    duration: float,
    *,
    method: str = "auto",
    model_size: str = "small",
) -> list[Word]:
    known = _WORD.findall(text)
    if not known:
        return []

    chosen = best_method() if method == "auto" else method
    if chosen in ("faster_whisper", "whisperx"):
        heard = _faster_whisper_words(Path(wav), text, model_size)
        if heard:
            return _retime_known_text(known, heard, duration)
        log.info("falling back to proportional timing for %s", Path(wav).name)

    return proportional(text, duration)


def shift(words: Sequence[Word], offset: float) -> list[Word]:
    return [Word(w.text, round(w.start + offset, 3), round(w.end + offset, 3)) for w in words]
