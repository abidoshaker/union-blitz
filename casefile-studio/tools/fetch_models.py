"""Download the local model files CaseFile Studio uses on CPU.

Run by install-deps.bat, but safe to run directly:

    .venv\\Scripts\\python.exe tools\\fetch_models.py
    .venv\\Scripts\\python.exe tools\\fetch_models.py --whisper-model base
    .venv\\Scripts\\python.exe tools\\fetch_models.py --retries 10

Downloads resume. A 310 MB file over a flaky connection is the normal case
this has to survive, so every URL is retried with backoff and each attempt
picks up from the bytes already on disk.
"""

from __future__ import annotations

import argparse
import os
import random
import ssl
import sys
import time
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
            "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main/voices.bin",
        ],
        20_000_000,  # ~27 MB
    ),
]

# YuNet face detector, used for the face-blur pass. Small, but it lives behind
# git-lfs, so the media.githubusercontent endpoint is the one that serves bytes
# rather than a pointer file.
FACE_FILES = [
    (
        "face_detection_yunet_2023mar.onnx",
        [
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
            "face_detection_yunet/face_detection_yunet_2023mar.onnx",
        ],
        200_000,
    ),
]

CHUNK = 1 << 20


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _opener() -> urllib.request.OpenerDirector:
    """Honour system proxies, and do not fall over on a corporate TLS bundle."""
    handlers: list = [urllib.request.ProxyHandler()]  # reads http_proxy/https_proxy
    bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if bundle and Path(bundle).exists():
        ctx = ssl.create_default_context(cafile=bundle)
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def _attempt(opener, url: str, part: Path, min_size: int, label: str) -> tuple[bool, str]:
    """One try at one URL, resuming from whatever is already in `part`."""
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": "casefile-studio"})
    if have:
        req.add_header("Range", f"bytes={have}-")

    try:
        with opener.open(req, timeout=120) as resp:
            resuming = getattr(resp, "status", 200) == 206
            if have and not resuming:
                # Server ignored the range request; start over rather than
                # appending a second copy of the file to the first.
                have = 0
                part.unlink(missing_ok=True)
            total = int(resp.headers.get("Content-Length") or 0) + have
            done = have
            last_shown = -1
            with open(part, "ab" if resuming else "wb") as fh:
                while True:
                    block = resp.read(CHUNK)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)
                    if total:
                        pct = done * 100 // total
                        if pct != last_shown and pct % 5 == 0:
                            print(f"\r      {label}  {pct}% of {human(total)}   ", end="", flush=True)
                            last_shown = pct
            print(f"\r{' ' * 62}\r", end="")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ssl.SSLError) as exc:
        return False, str(exc)

    if part.exists() and part.stat().st_size >= min_size:
        return True, ""
    return False, f"only got {human(part.stat().st_size if part.exists() else 0)}, expected {human(min_size)}+"


def download(urls: list[str], dest: Path, min_size: int, *, retries: int = 5) -> bool:
    if dest.exists() and dest.stat().st_size >= min_size:
        print(f"      {dest.name} already present, {human(dest.stat().st_size)}")
        return True

    part = dest.with_suffix(dest.suffix + ".part")
    opener = _opener()
    last_error = "no attempt made"

    for attempt in range(1, retries + 1):
        for url in urls:
            ok, error = _attempt(opener, url, part, min_size, dest.name)
            if ok:
                part.replace(dest)
                print(f"      {dest.name} downloaded, {human(dest.stat().st_size)}")
                return True
            last_error = error
            host = url.split("/")[2] if "//" in url else url
            print(f"      {dest.name}: {host} failed - {error[:90]}")

        if attempt < retries:
            wait = min(60, 3 * 2 ** (attempt - 1)) + random.uniform(0, 2)
            kept = part.stat().st_size if part.exists() else 0
            print(f"      retrying in {wait:.0f}s (attempt {attempt + 1} of {retries}"
                  + (f", resuming from {human(kept)}" if kept else "") + ")")
            time.sleep(wait)

    print()
    print(f"      COULD NOT DOWNLOAD {dest.name} after {retries} attempts.")
    print(f"      Last error: {last_error}")
    print( "      This is usually a proxy, a firewall, or antivirus blocking the")
    print( "      transfer. You can finish it by hand:")
    print(f"        1. Open {urls[0]}")
    print(f"        2. Save the file into {MODELS}")
    print(f"        3. Run doctor.bat to confirm it was picked up")
    print( "      Everything except the free offline voice works without it.")
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
        print("      Captions will use the built-in timing until this succeeds.")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-kokoro", action="store_true")
    ap.add_argument("--skip-face", action="store_true")
    ap.add_argument("--skip-whisper", action="store_true")
    ap.add_argument("--retries", type=int, default=5,
                    help="attempts per file; each one resumes where the last stopped")
    ap.add_argument(
        "--whisper-model",
        default="small",
        help="tiny/base/small/medium. 'small' is the sweet spot on CPU for hour-long audio.",
    )
    args = ap.parse_args()

    MODELS.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    if not args.skip_kokoro:
        print("      Kokoro-82M local voice, Apache-2.0, ~340 MB")
        for name, urls, min_size in KOKORO_FILES:
            if not download(urls, MODELS / name, min_size, retries=args.retries):
                failures.append(name)

    if not args.skip_face:
        print("      YuNet face detector for the blur pass, ~230 KB")
        for name, urls, min_size in FACE_FILES:
            if not download(urls, MODELS / name, min_size, retries=args.retries):
                failures.append(name)

    if not args.skip_whisper and not prefetch_whisper(args.whisper_model):
        failures.append("faster-whisper cache")

    if failures:
        print()
        print(f"      Incomplete: {', '.join(failures)}")
        print("      Re-running this script resumes rather than starting over.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
