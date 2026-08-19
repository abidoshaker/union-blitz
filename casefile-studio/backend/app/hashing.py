"""Content hashing. This is what makes re-rendering an hour-long video cheap.

A scene's audio hash covers only what changes the audio; its clip hash covers
only what changes the picture. Editing scene text therefore re-synthesises the
audio but does not force every clip in the project to re-render.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def _digest(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def file_hash(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()[:32]


def audio_hash(*, text: str, provider: str, voice_id: str, opts: dict[str, Any]) -> str:
    return _digest({"t": text, "p": provider, "v": voice_id, "o": opts})


def clip_hash(
    *,
    image_path: str,
    next_image_path: str | None,
    duration: float,
    kenburns: str,
    subtitle_text: str,
    overlays: dict[str, Any],
    render_opts: dict[str, Any],
) -> str:
    """Everything that changes a single scene clip's pixels, and nothing else."""
    return _digest(
        {
            "img": Path(image_path).name if image_path else "",
            "next": Path(next_image_path).name if next_image_path else "",
            "dur": round(duration, 3),
            "kb": kenburns,
            "sub": hashlib.sha256(subtitle_text.encode("utf-8")).hexdigest()[:16],
            "ov": overlays,
            "r": render_opts,
        }
    )
