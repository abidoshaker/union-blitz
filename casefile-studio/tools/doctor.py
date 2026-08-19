"""Environment check for CaseFile Studio, tuned for hour-long scripts.

    .venv\\Scripts\\python.exe tools\\doctor.py

Reports what is installed, what is missing, and what an hour-long render will
actually cost you in time and disk on this machine.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

OK = "  [ ok ] "
WARN = "  [warn] "
BAD = "  [FAIL] "

problems: list[str] = []
warnings: list[str] = []


def say(mark: str, msg: str) -> None:
    print(mark + msg)


def find_ffmpeg() -> str | None:
    env = os.environ.get("CASEFILE_FFMPEG_BIN")
    if env:
        cand = Path(env) / "ffmpeg.exe"
        if cand.exists():
            return str(cand)
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "CaseFileStudio" / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if local.exists():
        return str(local)
    return shutil.which("ffmpeg")


def check_python() -> None:
    v = sys.version_info
    if v[:2] >= (3, 10):
        say(OK, f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        say(BAD, f"Python {v.major}.{v.minor} is too old, need 3.10+")
        problems.append("python")


def check_ffmpeg() -> None:
    exe = find_ffmpeg()
    if not exe:
        say(BAD, "FFmpeg not found - no video can be rendered")
        problems.append("ffmpeg")
        return
    try:
        out = subprocess.run(
            [exe, "-hide_banner", "-buildconf"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except Exception as exc:
        say(BAD, f"FFmpeg found at {exe} but would not run: {exc}")
        problems.append("ffmpeg")
        return

    say(OK, f"FFmpeg at {exe}")
    for lib, why in (
        ("--enable-libx264", "H.264 encoding"),
        ("--enable-libass", "burned-in karaoke subtitles"),
    ):
        if lib in out:
            say(OK, f"  {lib[9:]} present - {why}")
        else:
            say(BAD, f"  {lib[9:]} MISSING - {why} will not work")
            problems.append(lib)

    # Hardware encoders are optional but change render time by an order of magnitude.
    try:
        enc = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=30).stdout
    except Exception:
        enc = ""
    hw = [n for n in ("h264_qsv", "h264_nvenc", "h264_amf") if n in enc]
    if hw:
        say(OK, f"  hardware encoders available: {', '.join(hw)}")
    else:
        say(WARN, "  no hardware encoder - CPU x264 only, which is the assumption anyway")


def check_module(name: str, label: str, required: bool) -> bool:
    if importlib.util.find_spec(name) is not None:
        say(OK, label)
        return True
    if required:
        say(BAD, f"{label} - MISSING")
        problems.append(name)
    else:
        say(WARN, f"{label} - not installed")
        warnings.append(name)
    return False


def check_models() -> None:
    onnx = MODELS / "kokoro-v1.0.onnx"
    voices = MODELS / "voices-v1.0.bin"
    if onnx.exists() and voices.exists():
        say(OK, f"Kokoro model files in {MODELS}")
    else:
        say(WARN, "Kokoro model files missing - run tools/fetch_models.py")
        warnings.append("kokoro-models")

    if (MODELS / "face_detection_yunet_2023mar.onnx").exists():
        say(OK, "Face detection model present - face blurring available")
    else:
        say(WARN, "Face detection model missing - run tools/fetch_models.py to enable blurring")
        warnings.append("face-model")


def check_resources() -> int:
    cores = os.cpu_count() or 1
    say(OK, f"{cores} logical CPU cores")
    if cores < 4:
        say(WARN, "  fewer than 4 cores - an hour-long render will be slow")
        warnings.append("cpu")

    try:
        import psutil

        gb = psutil.virtual_memory().total / (1024 ** 3)
        mark = OK if gb >= 8 else WARN
        say(mark, f"{gb:.1f} GB RAM")
        if gb < 8:
            warnings.append("ram")
    except ImportError:
        say(WARN, "psutil missing, cannot read RAM size")

    free = shutil.disk_usage(ROOT).free / (1024 ** 3)
    mark = OK if free >= 25 else (WARN if free >= 12 else BAD)
    say(mark, f"{free:.1f} GB free on the project drive")
    if free < 12:
        problems.append("disk")
    elif free < 25:
        warnings.append("disk")

    return cores


def longform_estimate(cores: int) -> None:
    print()
    print("  Hour-long script, what to expect on this machine")
    print("  " + "-" * 62)
    words = 9000          # ~60 min at ~150 wpm
    chars = words * 5.8   # incl. spaces and punctuation, ~= UTF-8 bytes for English
    fish_cost = chars / 1_000_000 * 15
    scenes = 240          # ~15 s per scene

    print(f"    script            ~{words:,} words, ~{int(chars):,} UTF-8 bytes")
    print(f"    scenes            ~{scenes} at ~15 s each")
    print(f"    Fish Audio TTS    ~${fish_cost:.2f} per video, a few minutes wall clock")
    print(f"    Kokoro local TTS  free, roughly {60 / 4:.0f}-{60 / 2:.0f} min on CPU")
    print(f"    caption timing    ~10-30 min with faster-whisper small int8")
    # 6.0 CPU-seconds per second of 1080p video, measured on veryfast/crf20
    # with a 2x Ken Burns upscale, spread across the worker pool.
    cpu_minutes = 60 * 6.0
    lo = cpu_minutes / max(cores, 1) * 0.7
    hi = cpu_minutes / max(cores, 1) * 1.8
    if hi >= 90:
        print(f"    video render      roughly {lo / 60:.1f}-{hi / 60:.1f} h at 1080p, preset veryfast")
    else:
        print(f"    video render      roughly {lo:.0f}-{hi:.0f} min at 1080p, preset veryfast")
    print(f"    scratch disk      ~2-4 GB of scene clips before the final mux")
    print()
    print("    Renders are checkpointed per scene, so a crash or a stop resumes")
    print("    instead of starting the hour over.")


def main() -> int:
    print()
    print("  CaseFile Studio - environment check")
    print("  " + "=" * 62)
    check_python()
    check_ffmpeg()
    print()
    check_module("fastapi", "FastAPI backend", True)
    check_module("sqlmodel", "SQLModel / SQLite", True)
    check_module("httpx", "httpx", True)
    check_module("cryptography", "encrypted key storage", True)
    check_module("soundfile", "audio I/O", True)
    print()
    check_module("cv2", "OpenCV, for face blurring", False)
    check_module("kokoro_onnx", "Kokoro local TTS", False)
    check_module("edge_tts", "Edge TTS free cloud voice", False)
    check_module("faster_whisper", "faster-whisper caption timing", False)
    check_module("whisperx", "WhisperX forced alignment, sub-100 ms", False)
    check_module("fish_audio_sdk", "Fish Audio SDK, optional - REST works without it", False)
    print()
    check_models()
    print()
    longform_estimate(check_resources())

    print()
    if problems:
        print(f"  {len(problems)} blocking problem(s): {', '.join(sorted(set(problems)))}")
        print("  Re-run install-deps.bat, then check install-log.txt.")
        return 1
    if warnings:
        print(f"  Ready, with optional pieces missing: {', '.join(sorted(set(warnings)))}")
        return 0
    print("  Everything checks out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
