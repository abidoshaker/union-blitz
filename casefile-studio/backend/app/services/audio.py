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
        stderr = ffmpeg.run(["-i", str(path), "-af", filt, "-f", "null", "-"])
    except ffmpeg.FFmpegError as exc:
        log.warning("loudness measurement failed: %s", exc)
        return None
    match = _LOUDNORM_JSON.search(stderr)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def build_master(
    *,
    narration: Path,
    dest: Path,
    music: Path | None = None,
    music_gain_db: float = -18.0,
    target_lufs: float | None = None,
    total_sec: float | None = None,
    on_progress=None,
) -> Path:
    """Narration + ducked music, two-pass loudnorm, encoded once to AAC.

    This is the only pass over the full hour of audio, and it is cheap because
    there is no video in it.
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

    args: list[str] = ["-i", str(narration)]
    if music and Path(music).exists():
        args += ["-stream_loop", "-1", "-i", str(music)]
        # sidechaincompress keys the music off the narration, so the bed drops
        # whenever the voice is present and lifts in the gaps.
        filter_complex = (
            f"[0:a]aformat=channel_layouts=stereo,{norm}[voice];"
            f"[voice]asplit=2[voice_out][key];"
            f"[1:a]aformat=channel_layouts=stereo,volume={music_gain_db}dB[bed];"
            f"[bed][key]sidechaincompress=threshold=0.05:ratio=8:attack=20:release=400[ducked];"
            f"[voice_out][ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix];"
            f"[mix]alimiter=limit=0.95[out]"
        )
        args += ["-filter_complex", filter_complex, "-map", "[out]"]
    else:
        args += ["-af", f"aformat=channel_layouts=stereo,{norm}"]

    args += ["-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), str(dest)]
    ffmpeg.run(args, on_progress=on_progress, total_sec=total_sec)
    return dest
