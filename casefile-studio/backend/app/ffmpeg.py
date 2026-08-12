"""Thin FFmpeg command builder and runner.

Everything about the render that is performance-critical is decided here:
the Ken Burns filter, the baked-in crossfade, and the encoder flags that make
the final concat a stream copy instead of a second full re-encode.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .config import settings


class FFmpegError(RuntimeError):
    def __init__(self, cmd: Sequence[str], returncode: int, stderr: str):
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stderr = stderr
        tail = "\n".join(stderr.strip().splitlines()[-12:])
        super().__init__(f"ffmpeg exited {returncode}\n{tail}")


@dataclass
class Capabilities:
    found: bool
    path: str = ""
    libx264: bool = False
    libass: bool = False
    hw_encoders: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.found and self.libx264 and self.libass

    def problems(self) -> list[str]:
        if not self.found:
            return ["FFmpeg was not found. Run install-deps.bat."]
        missing = []
        if not self.libx264:
            missing.append("libx264 (H.264 encoding)")
        if not self.libass:
            missing.append("libass (burned-in subtitles)")
        return [f"This FFmpeg build is missing {m}." for m in missing]


_caps: Capabilities | None = None


def capabilities(refresh: bool = False) -> Capabilities:
    global _caps
    if _caps is not None and not refresh:
        return _caps
    exe = settings.ffmpeg
    if not exe:
        _caps = Capabilities(found=False)
        return _caps
    try:
        buildconf = subprocess.run(
            [exe, "-hide_banner", "-buildconf"], capture_output=True, text=True, timeout=30
        ).stdout
        encoders = subprocess.run(
            [exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30
        ).stdout
    except (OSError, subprocess.SubprocessError):
        _caps = Capabilities(found=False)
        return _caps

    _caps = Capabilities(
        found=True,
        path=exe,
        libx264="--enable-libx264" in buildconf,
        libass="--enable-libass" in buildconf,
        hw_encoders=tuple(n for n in ("h264_qsv", "h264_nvenc", "h264_amf", "h264_videotoolbox") if n in encoders),
    )
    return _caps


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

def run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    on_progress: Callable[[float], None] | None = None,
    total_sec: float | None = None,
    timeout: float | None = None,
) -> str:
    """Run ffmpeg. Returns stderr; raises FFmpegError on failure."""
    exe = settings.ffmpeg
    if not exe:
        raise FFmpegError(args, -1, "FFmpeg is not installed")

    cmd = [exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    if on_progress and total_sec:
        cmd += ["-progress", "pipe:2", "-nostats"]

    proc = subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    stderr_parts: list[str] = []
    if on_progress and total_sec and proc.stderr is not None:
        for line in proc.stderr:
            stderr_parts.append(line)
            m = re.match(r"out_time_us=(\d+)", line.strip())
            if m:
                on_progress(min(1.0, int(m.group(1)) / 1e6 / max(total_sec, 0.001)))
        proc.wait(timeout=timeout)
        stderr = "".join(stderr_parts)
    else:
        _, stderr = proc.communicate(timeout=timeout)

    if proc.returncode != 0:
        raise FFmpegError(cmd, proc.returncode, stderr or "")
    return stderr or ""


def probe(path: str | Path) -> dict:
    exe = settings.ffprobe
    if not exe:
        return {}
    out = subprocess.run(
        [exe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60,
    ).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {}


def duration_of(path: str | Path) -> float:
    info = probe(path)
    try:
        return float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Ken Burns
# ---------------------------------------------------------------------------

KENBURNS_STYLES = ("zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static")


def pick_kenburns(style: str, index: int) -> str:
    """'auto' cycles the styles so consecutive scenes never move identically."""
    if style and style != "auto" and style in KENBURNS_STYLES:
        return style
    rotation = ("zoom_in", "pan_right", "zoom_out", "pan_left", "zoom_in", "pan_up", "zoom_out", "pan_down")
    return rotation[index % len(rotation)]


def kenburns_chain(
    *,
    style: str,
    out_w: int,
    out_h: int,
    fps: int,
    motion_offset_frames: int,
    motion_total_frames: int,
    upscale: float,
    amount: float = 0.18,
) -> str:
    """Build `scale,crop,zoompan` for one still.

    motion_offset/total let a scene's movement span two clips: the tail of the
    previous clip renders the first frames of this scene's motion during the
    crossfade, and this clip picks the motion up where that left off. Without
    that offset the crossfade would visibly replay the first moments of the
    next scene.
    """
    uw = max(out_w, int(out_w * upscale)) // 2 * 2
    uh = max(out_h, int(out_h * upscale)) // 2 * 2
    pre = f"scale={uw}:{uh}:force_original_aspect_ratio=increase,crop={uw}:{uh},setsar=1"

    span = max(motion_total_frames - 1, 1)
    p = f"((on+{motion_offset_frames})/{span})"
    centre_x, centre_y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"

    if style == "zoom_in":
        z, x, y = f"1+{amount}*{p}", centre_x, centre_y
    elif style == "zoom_out":
        z, x, y = f"{1 + amount}-{amount}*{p}", centre_x, centre_y
    elif style == "pan_right":
        z, x, y = f"{1 + amount}", f"(iw-iw/zoom)*{p}", centre_y
    elif style == "pan_left":
        z, x, y = f"{1 + amount}", f"(iw-iw/zoom)*(1-{p})", centre_y
    elif style == "pan_down":
        z, x, y = f"{1 + amount}", centre_x, f"(ih-ih/zoom)*{p}"
    elif style == "pan_up":
        z, x, y = f"{1 + amount}", centre_x, f"(ih-ih/zoom)*(1-{p})"
    else:  # static
        z, x, y = "1", centre_x, centre_y

    zoompan = (
        f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={out_w}x{out_h}:fps={fps}"
    )
    return f"{pre},{zoompan}"


def encoder_args(*, encoder: str, crf: int, preset: str, fps: int) -> list[str]:
    """Identical for every scene clip. That is what lets the final concat copy.

    A keyframe on frame 0 with scene-cut detection off means every clip is
    independently decodable and the concat demuxer can stitch them without
    touching the bitstream.
    """
    args = ["-c:v", encoder]
    if encoder == "libx264":
        args += ["-preset", preset, "-crf", str(crf)]
    else:  # hardware encoders take a quality flag instead of crf
        args += ["-global_quality", str(crf)]
    args += [
        "-pix_fmt", "yuv420p",
        "-r", str(fps),
        "-g", str(fps * 2),
        "-keyint_min", str(fps * 2),
        "-sc_threshold", "0",
        "-force_key_frames", "expr:eq(n,0)",
        "-movflags", "+faststart",
    ]
    return args


def format_cmd(args: Sequence[str]) -> str:
    return " ".join(shlex.quote(a) for a in args)
