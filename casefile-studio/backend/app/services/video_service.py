"""Fetching and preparing video clips.

Downloads are capped, trimmed and re-encoded to one normalised intermediate
before they ever reach the renderer. A stock clip can be 4K, 60 seconds and
200 MB for a scene that needs twelve seconds at 1080p; normalising once here
keeps every later stage cheap and predictable.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .. import ffmpeg
from ..config import settings
from ..providers.image.base import AssetCandidate
from ..providers.video import VideoUnavailable

log = logging.getLogger("casefile.video")

MAX_DOWNLOAD_BYTES = 150_000_000
# Long enough to cover any single scene plus a choice of in-point, short enough
# that a hundred clips do not fill a drive.
MAX_CLIP_SEC = 40.0


@dataclass
class StoredVideo:
    path: Path
    poster: Path
    duration: float
    width: int
    height: int
    has_audio: bool
    provider: str
    license: str
    attribution: str
    source_url: str
    content_hash: str
    audio_path: Path | None = None
    face_tracks: list = field(default_factory=list)


def _video_dir(project_id: int) -> Path:
    path = settings.project_dir(project_id) / "assets" / "video"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download(url: str, dest: Path, max_bytes: int = MAX_DOWNLOAD_BYTES) -> None:
    """Stream to disk, aborting if the source turns out to be enormous."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with httpx.Client(timeout=180, follow_redirects=True,
                      headers={"User-Agent": "CaseFileStudio/1.0"}) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            declared = int(resp.headers.get("Content-Length") or 0)
            if declared and declared > max_bytes:
                raise VideoUnavailable(
                    f"That clip is {declared / 1e6:.0f} MB, over the {max_bytes / 1e6:.0f} MB limit."
                )
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes(1 << 20):
                    written += len(chunk)
                    if written > max_bytes:
                        fh.close()
                        dest.unlink(missing_ok=True)
                        raise VideoUnavailable("Clip exceeded the download limit part-way through.")
                    fh.write(chunk)


def store_candidate(
    project_id: int,
    candidate: AssetCandidate,
    *,
    max_seconds: float = MAX_CLIP_SEC,
    target_width: int | None = None,
) -> StoredVideo:
    """Fetch, trim and normalise one clip. Cached by source URL."""
    if candidate.kind != "video":
        raise VideoUnavailable("That candidate is not a video.")
    if not candidate.url:
        raise VideoUnavailable("Video candidate has no URL.")

    target_width = target_width or settings.width
    digest = hashlib.sha256(
        f"{candidate.url}|{max_seconds}|{target_width}".encode()
    ).hexdigest()[:32]
    directory = _video_dir(project_id)
    dest = directory / f"{digest}.mp4"
    poster = directory / f"{digest}.jpg"

    if not dest.exists():
        raw = directory / f".{digest}.raw"
        try:
            _download(candidate.url, raw)
            _normalise(raw, dest, max_seconds=max_seconds, target_width=target_width)
        finally:
            raw.unlink(missing_ok=True)

    if not poster.exists():
        ffmpeg.run(["-i", str(dest), "-frames:v", "1", "-q:v", "3", str(poster)])

    info = ffmpeg.probe(dest)
    streams = info.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    return StoredVideo(
        path=dest, poster=poster,
        duration=ffmpeg.duration_of(dest),
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        has_audio=has_audio,
        provider=candidate.provider,
        license=candidate.license,
        attribution=candidate.attribution,
        source_url=candidate.url,
        content_hash=digest,
    )


def _normalise(src: Path, dest: Path, *, max_seconds: float, target_width: int) -> None:
    """One codec, one pixel format, sane size. Audio is kept if present."""
    tmp = dest.with_suffix(".tmp.mp4")
    args = [
        "-t", f"{max_seconds:.2f}",
        "-i", str(src),
        "-vf", (
            f"scale='min({target_width},iw)':-2:flags=lanczos,"
            f"fps=fps=min(source_fps\\,30),format=yuv420p,setsar=1"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        ffmpeg.run(args)
    except ffmpeg.FFmpegError:
        # Some archival sources have no audio track at all, which makes the
        # audio mapping above fail. Retry video-only.
        ffmpeg.run([
            "-t", f"{max_seconds:.2f}", "-i", str(src),
            "-vf", (
                f"scale='min({target_width},iw)':-2:flags=lanczos,"
                f"fps=fps=min(source_fps\\,30),format=yuv420p,setsar=1"
            ),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an",
            "-movflags", "+faststart", str(tmp),
        ])
    tmp.replace(dest)


def extract_audio(video: Path, dest: Path, *, sample_rate: int = 48000) -> float:
    """Pull a clip's audio out as mono PCM at the project's sample rate.

    Same format as TTS output, so it can drop straight into the narration
    timeline as a soundbite or into the ambient bed.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([
        "-i", str(video),
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le",
        str(dest),
    ])
    return ffmpeg.duration_of(dest)


def has_audible_audio(video: Path, *, floor_db: float = -50.0) -> bool:
    """True if there is an audio track with something actually on it.

    Stock clips frequently carry a silent track, and treating that as a
    soundbite would pause the narration for nothing.
    """
    try:
        stderr = ffmpeg.run(
            ["-i", str(video), "-af", "volumedetect", "-f", "null", "-"], loglevel="info"
        )
    except ffmpeg.FFmpegError:
        return False
    for line in stderr.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0].strip()) > floor_db
            except (ValueError, IndexError):
                return False
    return False


# ---------------------------------------------------------------------------
# Face regions, cached per clip
# ---------------------------------------------------------------------------

def face_tracks(video: Path, *, start: float = 0.0, duration: float | None = None) -> list:
    """Detected face regions, cached beside the clip.

    Detection is the slow part of the blur pass, and a clip reused across
    several scenes should only pay for it once.
    """
    from . import vision

    cache = video.with_suffix(".faces.json")
    key = f"{start:.2f}:{duration or 0:.2f}"
    if cache.exists():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            if payload.get("key") == key:
                return [
                    vision.Track(
                        box=vision.Box(**t["box"]), start=t["start"], end=t["end"]
                    )
                    for t in payload["tracks"]
                ]
        except (ValueError, KeyError, TypeError):
            pass

    tracks = vision.detect_video_faces(video, start=start, duration=duration)
    cache.write_text(json.dumps({
        "key": key,
        "tracks": [
            {"box": {"x": t.box.x, "y": t.box.y, "w": t.box.w, "h": t.box.h, "score": t.box.score},
             "start": t.start, "end": t.end}
            for t in tracks
        ],
    }), encoding="utf-8")
    return tracks


def pick_in_point(duration: float, needed: float) -> float:
    """Where to start inside a clip.

    A couple of seconds in, because stock footage often opens on a fade or a
    static frame - but never so far in that the clip runs out.
    """
    if duration <= needed:
        return 0.0
    return min(2.0, max(0.0, duration - needed))
