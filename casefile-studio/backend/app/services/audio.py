"""Narration assembly, music ducking, loudness.

Scene boundaries come from the WAV lengths we generated, never from ASR. That
is exact, costs nothing, and cannot drift over an hour.
"""

from __future__ import annotations

import json
import logging
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .. import ffmpeg
from ..config import settings

log = logging.getLogger("casefile.audio")

SAMPLE_RATE = 48000


@dataclass
class Timeline:
    """Where each scene sits in the finished narration."""
    starts: list[float]
    ends: list[float]
    total: float


def concat_wavs(paths: Iterable[Path], dest: Path, *, gap_sec: float = 0.25) -> Timeline:
    """Stream-concatenate scene WAVs, inserting a breath between scenes.

    Written frame by frame rather than loaded into numpy: an hour of 48k mono
    PCM is ~350 MB, and there is no reason to hold it in memory.
    """
    paths = list(paths)
    starts: list[float] = []
    ends: list[float] = []
    dest.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(dest), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        cursor = 0  # frames
        gap_frames = int(gap_sec * SAMPLE_RATE)
        silence = b"\x00\x00" * gap_frames

        for i, path in enumerate(paths):
            with wave.open(str(path), "rb") as src:
                if src.getframerate() != SAMPLE_RATE or src.getnchannels() != 1:
                    raise ValueError(
                        f"{path.name} is {src.getframerate()}Hz/{src.getnchannels()}ch; "
                        "scene audio must be normalised to mono 48k first"
                    )
                starts.append(cursor / SAMPLE_RATE)
                remaining = src.getnframes()
                while remaining > 0:
                    block = src.readframes(min(remaining, SAMPLE_RATE))
                    if not block:
                        break
                    out.writeframes(block)
                    remaining -= len(block) // 2
                cursor += src.getnframes()
                ends.append(cursor / SAMPLE_RATE)

            if gap_frames and i < len(paths) - 1:
                out.writeframes(silence)
                cursor += gap_frames

    return Timeline(starts=starts, ends=ends, total=cursor / SAMPLE_RATE)


# ---------------------------------------------------------------------------
# Loudness
# ---------------------------------------------------------------------------

_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.S)


def measure_loudness(path: Path, target_lufs: float) -> dict | None:
    """Pass one of two-pass loudnorm."""
    filt = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json"
    try:
        # loudnorm prints its JSON at info level, so the default error-only
        # log would hide the very numbers this pass exists to collect.
        stderr = ffmpeg.run(["-i", str(path), "-af", filt, "-f", "null", "-"], loglevel="info")
    except ffmpeg.FFmpegError as exc:
        log.warning("loudness measurement failed: %s", exc)
        return None
    match = _LOUDNORM_JSON.search(stderr)
    if not match:
        return None
    try:
        measured = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    # Silence measures as -inf, and ffmpeg rejects that as measured_I. This is
    # reachable in normal use: the draft voice is silent, and a chapter can be
    # nothing but a soundbite with no voiceover in it. Fall back to the
    # single-pass filter rather than building a command that cannot run.
    required = ("input_i", "input_tp", "input_lra", "input_thresh")
    try:
        values = {key: float(measured[key]) for key in required}
        values["target_offset"] = float(measured.get("target_offset", 0.0))
    except (KeyError, TypeError, ValueError):
        return None
    if any(v != v or v in (float("inf"), float("-inf")) for v in values.values()):
        log.info("loudness measurement was not finite (silent audio); using single-pass loudnorm")
        return None
    if values["input_i"] <= -70.0:
        log.info("narration is effectively silent; skipping the two-pass normalisation")
        return None
    return measured


def silence(seconds: float, dest: Path, *, sample_rate: int = SAMPLE_RATE) -> Path:
    """A silent mono WAV of an exact length."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round(seconds * sample_rate)))
    with wave.open(str(dest), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(b"\x00\x00" * frames)
    return dest


def fit(src: Path, dest: Path, seconds: float, *, sample_rate: int = SAMPLE_RATE) -> Path:
    """Trim or pad a WAV to an exact length.

    The ambient bed has to line up sample-for-sample with the narration track,
    so every segment is forced to its scene's length rather than trusted.
    """
    target = max(1, int(round(seconds * sample_rate)))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(src), "rb") as reader, wave.open(str(dest), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        remaining = target
        while remaining > 0:
            block = reader.readframes(min(remaining, sample_rate))
            if not block:
                break
            out.writeframes(block)
            remaining -= len(block) // 2
        if remaining > 0:
            out.writeframes(b"\x00\x00" * remaining)
    return dest


def build_master(
    *,
    narration: Path,
    dest: Path,
    music: Path | None = None,
    ambient: Path | None = None,
    music_gain_db: float = -18.0,
    ambient_gain_db: float = -9.0,
    target_lufs: float | None = None,
    total_sec: float | None = None,
    on_progress=None,
) -> Path:
    """Narration, plus ducked music and ducked clip ambience, to one AAC track.

    Three sources, one pass over the hour, and it is cheap because there is no
    video in it. Both the music bed and any ambience from sourced footage are
    keyed off the voice, so nothing competes with the narration.

    Note what is *not* here: a soundbite. When a clip is meant to speak, its
    audio is placed in the narration track itself, in the scene's slot, so the
    voiceover simply does not exist at that moment. Overlap is impossible by
    construction rather than mixed around.
    """
    target = settings.loudness_lufs if target_lufs is None else target_lufs
    measured = measure_loudness(narration, target)

    if measured:
        # Second pass with measured values: linear, no pumping.
        norm = (
            f"loudnorm=I={target}:TP=-1.5:LRA=11"
            f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
            f":offset={measured.get('target_offset', 0)}:linear=true"
        )
    else:
        norm = f"loudnorm=I={target}:TP=-1.5:LRA=11"

    beds: list[tuple[Path, float, bool]] = []      # (path, gain_db, loop)
    if music and Path(music).exists():
        beds.append((Path(music), music_gain_db, True))
    if ambient and Path(ambient).exists():
        beds.append((Path(ambient), ambient_gain_db, False))

    args: list[str] = ["-i", str(narration)]
    if not beds:
        args += ["-af", f"aformat=channel_layouts=stereo,{norm}"]
    else:
        for path, _gain, loop in beds:
            if loop:
                args += ["-stream_loop", "-1"]
            args += ["-i", str(path)]

        parts = [f"[0:a]aformat=channel_layouts=stereo,{norm}[voice]"]
        # One copy of the voice to output, plus one sidechain key per bed.
        keys = "".join(f"[key{i}]" for i in range(len(beds)))
        parts.append(f"[voice]asplit={len(beds) + 1}[voice_out]{keys}")

        mix_labels = ["voice_out"]
        for i, (_path, gain, _loop) in enumerate(beds):
            parts.append(f"[{i + 1}:a]aformat=channel_layouts=stereo,volume={gain}dB[bed{i}]")
            parts.append(
                f"[bed{i}][key{i}]sidechaincompress="
                f"threshold=0.05:ratio=8:attack=20:release=400[duck{i}]"
            )
            mix_labels.append(f"duck{i}")

        parts.append(
            "".join(f"[{label}]" for label in mix_labels)
            + f"amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0:normalize=0[mix]"
        )
        # apad plus an explicit -t pins the result to the narration's length.
        # sidechaincompress emits min(main, sidechain), so a bed even slightly
        # short would otherwise cut the whole mix off early.
        parts.append("[mix]apad,alimiter=limit=0.95[out]")
        args += ["-filter_complex", ";".join(parts), "-map", "[out]"]

    if total_sec:
        args += ["-t", f"{total_sec:.3f}"]
    args += ["-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), str(dest)]
    ffmpeg.run(args, on_progress=on_progress, total_sec=total_sec)
    return dest
