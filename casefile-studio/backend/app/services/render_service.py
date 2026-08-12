"""Video assembly.

The shape of this module is the whole long-form argument:

  * every scene renders to a finished, self-contained clip - Ken Burns,
    crossfade tail, burned captions and overlays, all baked in;
  * every clip uses byte-identical encoder settings and opens on a keyframe;
  * the final assembly is therefore a stream copy plus one audio mux, which
    takes seconds on a 60-minute video instead of a second full re-encode.

Scene clips render in parallel across cores and are cached by content hash, so
editing scene 138 of 240 re-renders two clips and copies the rest.
"""

from __future__ import annotations

import logging
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .. import ffmpeg
from ..config import settings
from ..hashing import clip_hash
from .align_service import Word
from .subtitle_service import DEFAULT_STYLE, write_scene_ass

log = logging.getLogger("casefile.render")


class DiskSpaceError(RuntimeError):
    pass


@dataclass
class RenderOpts:
    width: int = 0
    height: int = 0
    fps: int = 0
    crf: int = 0
    preset: str = ""
    encoder: str = ""
    upscale: float = 0.0
    transition_sec: float = -1.0
    subtitle_style: str = DEFAULT_STYLE
    karaoke: bool = True
    burn_subtitles: bool = True

    def __post_init__(self) -> None:
        self.width = self.width or settings.width
        self.height = self.height or settings.height
        self.fps = self.fps or settings.fps
        self.crf = self.crf or settings.crf
        self.preset = self.preset or settings.preset
        self.encoder = self.encoder or settings.encoder
        self.upscale = self.upscale or settings.kenburns_upscale
        if self.transition_sec < 0:
            self.transition_sec = settings.transition_sec

    def signature(self) -> dict:
        return {
            "w": self.width, "h": self.height, "fps": self.fps, "crf": self.crf,
            "preset": self.preset, "enc": self.encoder, "up": self.upscale,
            "style": self.subtitle_style, "kara": self.karaoke, "subs": self.burn_subtitles,
        }


@dataclass
class SceneSpec:
    """Everything one clip needs. Deliberately free of DB objects so the
    render layer stays testable without a database."""

    index: int
    image: Path
    frames: int
    kenburns: str = "auto"
    words: list[Word] = field(default_factory=list)
    banner: str = ""
    lower_third: str = ""
    # Filled in by plan_transitions.
    next_image: Path | None = None
    next_kenburns: str = "auto"
    transition_frames: int = 0
    motion_offset_frames: int = 0
    motion_total_frames: int = 0


def frame_plan(starts: Sequence[float], total: float, fps: int) -> list[int]:
    """Frame counts derived from the audio timeline, not from rounded durations.

    Quantising each boundary to the nearest frame - rather than each duration
    independently - keeps the video's cumulative time locked to the audio's.
    Over 240 scenes, per-scene rounding would drift by a visible amount.
    """
    marks = [int(round(s * fps)) for s in starts] + [int(round(total * fps))]
    return [max(1, marks[i + 1] - marks[i]) for i in range(len(marks) - 1)]


def plan_transitions(specs: list[SceneSpec], opts: RenderOpts) -> None:
    """Give each scene its outgoing crossfade and its incoming motion offset."""
    fps = opts.fps
    for i, spec in enumerate(specs):
        nxt = specs[i + 1] if i + 1 < len(specs) else None
        if nxt is None or opts.transition_sec <= 0:
            spec.transition_frames = 0
            spec.next_image = None
            continue
        # A transition may never eat more than 40% of either neighbour.
        limit = int(min(spec.frames, nxt.frames) * 0.4)
        spec.transition_frames = max(0, min(int(round(opts.transition_sec * fps)), limit))
        spec.next_image = nxt.image if spec.transition_frames else None
        spec.next_kenburns = nxt.kenburns

    for i, spec in enumerate(specs):
        incoming = specs[i - 1].transition_frames if i > 0 else 0
        spec.motion_offset_frames = incoming
        spec.motion_total_frames = spec.frames + incoming


# ---------------------------------------------------------------------------
# One scene clip
# ---------------------------------------------------------------------------

def render_scene_clip(
    spec: SceneSpec,
    workdir: Path,
    opts: RenderOpts,
    *,
    force: bool = False,
) -> tuple[Path, bool]:
    """Render (or reuse) one clip. Returns (path, was_cached)."""
    workdir.mkdir(parents=True, exist_ok=True)

    subtitle_text = ""
    if opts.burn_subtitles and spec.words:
        subtitle_text = " ".join(w.text for w in spec.words)

    digest = clip_hash(
        image_path=str(spec.image),
        next_image_path=str(spec.next_image) if spec.next_image else None,
        duration=spec.frames / opts.fps,
        kenburns=f"{spec.kenburns}:{spec.next_kenburns}",
        subtitle_text=subtitle_text + spec.banner + spec.lower_third,
        overlays={"banner": spec.banner, "lower": spec.lower_third},
        render_opts={
            **opts.signature(),
            "frames": spec.frames,
            "tf": spec.transition_frames,
            "mo": spec.motion_offset_frames,
            "mt": spec.motion_total_frames,
            "wt": [(round(w.start, 2), round(w.end, 2)) for w in spec.words],
        },
    )
    clip = workdir / f"scene_{spec.index:05d}_{digest}.mp4"
    if clip.exists() and not force and clip.stat().st_size > 2048:
        return clip, True

    for stale in workdir.glob(f"scene_{spec.index:05d}_*.mp4"):
        stale.unlink(missing_ok=True)

    fps = opts.fps
    style = ffmpeg.pick_kenburns(spec.kenburns, spec.index)

    args: list[str] = [
        "-loop", "1", "-framerate", str(fps), "-t", f"{spec.frames / fps + 1:.3f}",
        "-i", str(spec.image),
    ]
    chains = [
        "[0:v]"
        + ffmpeg.kenburns_chain(
            style=style, out_w=opts.width, out_h=opts.height, fps=fps,
            motion_offset_frames=spec.motion_offset_frames,
            motion_total_frames=max(spec.motion_total_frames, spec.frames),
            upscale=opts.upscale,
        )
        + f",trim=end_frame={spec.frames},setpts=PTS-STARTPTS[base]"
    ]
    last = "base"

    if spec.transition_frames and spec.next_image:
        next_style = ffmpeg.pick_kenburns(spec.next_kenburns, spec.index + 1)
        args += [
            "-loop", "1", "-framerate", str(fps),
            "-t", f"{spec.transition_frames / fps + 1:.3f}", "-i", str(spec.next_image),
        ]
        # The next scene's motion starts here, in this clip's tail, and the
        # next clip picks it up at motion_offset_frames. Without that handoff
        # the crossfade would replay the next scene's opening.
        chains.append(
            "[1:v]"
            + ffmpeg.kenburns_chain(
                style=next_style, out_w=opts.width, out_h=opts.height, fps=fps,
                motion_offset_frames=0,
                motion_total_frames=max(spec.transition_frames, 1),
                upscale=opts.upscale,
            )
            + f",trim=end_frame={spec.transition_frames},setpts=PTS-STARTPTS[incoming]"
        )
        offset = (spec.frames - spec.transition_frames) / fps
        chains.append(
            f"[base][incoming]xfade=transition=fade:"
            f"duration={spec.transition_frames / fps:.3f}:offset={offset:.3f}[faded]"
        )
        last = "faded"

    if subtitle_text or spec.banner or spec.lower_third:
        ass_path = workdir / f"scene_{spec.index:05d}.ass"
        write_scene_ass(
            spec.words, ass_path,
            width=opts.width, height=opts.height,
            style=opts.subtitle_style, karaoke=opts.karaoke,
            banner=spec.banner, lower_third=spec.lower_third,
            banner_until=spec.frames / fps,
        )
        # Run with cwd=workdir and a bare filename: ASS paths inside a
        # filtergraph need triple escaping on Windows ("C\\:/path"), and this
        # sidesteps the whole problem.
        chains.append(f"[{last}]ass={ass_path.name}[vout]")
        last = "vout"

    tmp = workdir / f".{clip.stem}.tmp.mp4"
    args += [
        "-filter_complex", ";".join(chains),
        "-map", f"[{last}]",
        *ffmpeg.encoder_args(encoder=opts.encoder, crf=opts.crf, preset=opts.preset, fps=fps),
        "-frames:v", str(spec.frames),
        "-an",
        tmp.name,
    ]
    ffmpeg.run(args, cwd=workdir)
    tmp.replace(clip)
    return clip, False


def render_scene_clips(
    specs: list[SceneSpec],
    workdir: Path,
    opts: RenderOpts,
    *,
    workers: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], None] | None = None,
    skip: set[int] | None = None,
) -> list[Path]:
    """Render every clip in parallel. This is the CPU-bound hour."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    workers = workers or settings.render_workers
    results: dict[int, Path] = {}
    done = 0

    def work(spec: SceneSpec) -> tuple[int, Path]:
        if should_stop:
            should_stop()
        path, _cached = render_scene_clip(spec, workdir, opts)
        return spec.index, path

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(work, s) for s in specs if not skip or s.index not in skip]
        for future in as_completed(futures):
            index, path = future.result()
            results[index] = path
            done += 1
            if on_progress:
                on_progress(done, len(futures))

    ordered: list[Path] = []
    for spec in specs:
        if spec.index in results:
            ordered.append(results[spec.index])
        else:
            existing = sorted(workdir.glob(f"scene_{spec.index:05d}_*.mp4"))
            if not existing:
                raise FileNotFoundError(f"scene {spec.index} has no clip and was skipped")
            ordered.append(existing[0])
    return ordered


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def concat_clips(clips: Sequence[Path], dest: Path, *, audio: Path | None = None) -> Path:
    """Stream-copy concat plus the single audio track.

    The concat demuxer, not the concat protocol, and never a 240-input
    filtergraph - that would exhaust memory long before it finished.
    """
    if not clips:
        raise ValueError("nothing to concatenate")
    dest.parent.mkdir(parents=True, exist_ok=True)

    # The list file lives beside the clips and names them relatively, because
    # that is the working directory ffmpeg is given.
    workdir = Path(clips[0]).parent
    listing = workdir / f".{dest.stem}.concat.txt"
    listing.write_text(
        "".join(f"file '{Path(c).name}'\n" for c in clips), encoding="utf-8"
    )

    args = ["-f", "concat", "-safe", "0", "-i", listing.name]
    if audio:
        args += ["-i", str(Path(audio).resolve())]
    args += ["-c:v", "copy"]
    if audio:
        args += ["-c:a", "copy", "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart", str(Path(dest).resolve())]

    ffmpeg.run(args, cwd=workdir)
    listing.unlink(missing_ok=True)
    return dest


def render_shorts(
    source: Path,
    dest: Path,
    *,
    start: float,
    duration: float,
    opts: RenderOpts,
    subtitles: Path | None = None,
) -> Path:
    """9:16 vertical cut. Re-encoded from the master with a centre crop."""
    chain = (
        f"crop=ih*9/16:ih,scale=1080:1920:flags=lanczos,setsar=1"
    )
    if subtitles and subtitles.exists():
        chain += f",ass={subtitles.name}"
    args = [
        "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(source.resolve()),
        "-vf", chain,
        *ffmpeg.encoder_args(encoder=opts.encoder, crf=opts.crf, preset=opts.preset, fps=opts.fps),
        "-c:a", "aac", "-b:a", "160k",
        str(dest.resolve()),
    ]
    ffmpeg.run(args, cwd=(subtitles.parent if subtitles else dest.parent))
    return dest


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def estimate_disk_bytes(scene_count: int, total_sec: float, opts: RenderOpts) -> int:
    """Clips plus master plus audio, rounded up hard.

    An hour-long render that dies at scene 200 because the drive filled is the
    worst possible failure, so this is deliberately pessimistic.
    """
    megabits_per_sec = 9.0 if opts.crf <= 20 else 6.0
    clip_bytes = total_sec * megabits_per_sec * 125_000 * 1.35
    master_bytes = total_sec * megabits_per_sec * 125_000
    audio_bytes = total_sec * 24_000 * 2
    return int(clip_bytes + master_bytes + audio_bytes)


def preflight(scene_count: int, total_sec: float, opts: RenderOpts, target: Path) -> dict:
    caps = ffmpeg.capabilities()
    problems = caps.problems()
    if opts.burn_subtitles and caps.found and not caps.libass:
        problems.append("Subtitles are switched on but this FFmpeg cannot burn them.")

    needed = estimate_disk_bytes(scene_count, total_sec, opts)
    target.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(target).free
    if free < needed * 1.5:
        problems.append(
            f"Not enough disk: this render needs about {needed / 1e9:.1f} GB of "
            f"working space and {free / 1e9:.1f} GB is free."
        )

    cores = max(1, settings.render_workers)
    # Measured on 1080p Ken Burns at veryfast: roughly 8x realtime of CPU work,
    # spread across the pool. Shown as a range because it is a planning number.
    cpu_seconds = total_sec * 8.0
    return {
        "ok": not problems,
        "problems": problems,
        "estimated_disk_bytes": needed,
        "free_disk_bytes": free,
        "estimated_render_sec_low": cpu_seconds / cores * 0.6,
        "estimated_render_sec_high": cpu_seconds / cores * 2.0,
        "workers": cores,
        "hw_encoders": list(caps.hw_encoders),
    }


# ---------------------------------------------------------------------------
# Chapters and ad breaks
# ---------------------------------------------------------------------------

def format_timecode(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def write_chapters_txt(chapters: Sequence[tuple[str, float]], dest: Path) -> Path:
    """YouTube description format. The first entry must be 00:00 or YouTube
    ignores the whole list."""
    lines = []
    for i, (title, start) in enumerate(sorted(chapters, key=lambda c: c[1])):
        stamp = "00:00" if i == 0 else format_timecode(start)
        lines.append(f"{stamp} {title}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def suggest_ad_breaks(
    scene_starts: Sequence[float],
    total: float,
    *,
    every_sec: float = 540.0,
    edge_guard: float = 120.0,
) -> list[float]:
    """Mid-roll positions at scene boundaries, roughly every 9 minutes.

    On an hour-long video mid-rolls are most of the revenue, and YouTube places
    them badly on its own. Never inside a sentence, never near either end.
    """
    if total < every_sec + edge_guard * 2:
        return []
    breaks: list[float] = []
    target = every_sec
    while target < total - edge_guard:
        candidate = min(
            (s for s in scene_starts if s > edge_guard),
            key=lambda s: abs(s - target),
            default=None,
        )
        if candidate is None:
            break
        if not breaks or candidate - breaks[-1] > every_sec * 0.6:
            breaks.append(round(candidate, 2))
        target += every_sec
    return breaks
