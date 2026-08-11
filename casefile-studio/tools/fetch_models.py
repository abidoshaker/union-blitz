"""Download the local model files CaseFile Studio uses on CPU.

Run by install-deps.bat, but safe to run directly:

    .venv\\Scripts\\python.exe tools\\fetch_models.py
    .venv\\Scripts\\python.exe tools\\fetch_models.py --whisper-model base

Downloads are resumable and skipped if the file is already the right size, so
re-running after a dropped connection costs nothing.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

# Kokoro-82M, Apache-2.0. The ONNX build is what makes it CPU-fast.
KOKORO_FILES = [
    (
        "kokoro-v1.0.onnx",
        [
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
            "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main/onnx/model.onnx",
        ],
        300_000_000,  # sanity floor, real file is ~310 MB
    ),
    (
        "voices-v1.0.bin",
        [
            "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
        ],
        20_000_000,  # ~26 MB
    ),
]

CHUNK = 1 << 20


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def download(urls: list[str], dest: Path, min_size: int) -> bool:
    if dest.exists() and dest.stat().st_size >= min_size:
        print(f"      {dest.name} already present, {human(dest.stat().st_size)}")
        return True

    part = dest.with_suffix(dest.suffix + ".part")
    for url in urls:
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"User-Agent": "casefile-studio"})
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resuming = resp.status == 206
                if have and not resuming:
                    have = 0
                    part.unlink(missing_ok=True)
                total = int(resp.headers.get("Content-Length") or 0) + have
                mode = "ab" if resuming else "wb"
                done = have
                print(f"      {dest.name}  {human(total) if total else '?'}", end="", flush=True)
                with open(part, mode) as fh:
                    while True:
                        block = resp.read(CHUNK)
                        if not block:
                            break
                        fh.write(block)
                        done += len(block)
                        if total:
                            print(f"\r      {dest.name}  {done * 100 // total}%  ", end="", flush=True)
                print("\r" + " " * 60, end="\r")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"\r      {dest.name}: {exc}")
            continue

        if part.exists() and part.stat().st_size >= min_size:
            part.replace(dest)
            print(f"      {dest.name} downloaded, {human(dest.stat().st_size)}")
            return True
        print(f"      {dest.name}: file was smaller than expected, trying next mirror")
        part.unlink(missing_ok=True)

    print(f"      {dest.name}: FAILED from every mirror")
    return False


def prefetch_whisper(size: str) -> bool:
    """Warm the faster-whisper cache so the first render does not stall."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("      faster-whisper not installed, skipping")
        return True
    try:
        print(f"      caching faster-whisper '{size}' (int8)")
        WhisperModel(size, device="cpu", compute_type="int8")
        print("      cached")
        return True
    except Exception as exc:  # network, disk, HF outage
        print(f"      could not cache the whisper model: {exc}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-kokoro", action="store_true")
    ap.add_argument("--skip-whisper", action="store_true")
    ap.add_argument(
        "--whisper-model",
        default="small",
        help="tiny/base/small/medium. 'small' is the sweet spot on CPU for hour-long audio.",
    )
    args = ap.parse_args()

    MODELS.mkdir(parents=True, exist_ok=True)
    ok = True

    if not args.skip_kokoro:
        print("      Kokoro-82M local voice, Apache-2.0, ~340 MB")
        for name, urls, min_size in KOKORO_FILES:
            ok &= download(urls, MODELS / name, min_size)

    if not args.skip_whisper:
        ok &= prefetch_whisper(args.whisper_model)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
