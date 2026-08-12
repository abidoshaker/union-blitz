"""Narration synthesis for a whole project.

At hour-long scale this is 240-ish provider calls against Fish Audio's
5-concurrent Starter limit, so it is a bounded-concurrency queue with retry,
a per-scene cache, and a spend ceiling - not a for loop.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .. import ffmpeg
from ..config import settings
from ..hashing import audio_hash
from ..providers.tts import TTSOpts, TTSProvider, TTSUnavailable, get_provider

log = logging.getLogger("casefile.tts")

SAMPLE_RATE = 48000


class SpendCeilingReached(RuntimeError):
    pass


@dataclass
class SceneAudio:
    scene_id: int
    path: Path
    duration: float
    cached: bool
    cost: float


class CostMeter:
    """Stops a runaway batch. 240 scenes is a lot of money to spend by accident."""

    def __init__(self, ceiling: float | None) -> None:
        self.ceiling = ceiling
        self.spent = 0.0
        self._lock = threading.Lock()

    def reserve(self, amount: float) -> None:
        with self._lock:
            if self.ceiling is not None and self.spent + amount > self.ceiling + 1e-9:
                raise SpendCeilingReached(
                    f"This batch would cost ${self.spent + amount:.2f}, over the "
                    f"${self.ceiling:.2f} ceiling. Raise the ceiling or use a free voice."
                )
            self.spent += amount


_key_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
_key_locks_guard = threading.Lock()


@contextmanager
def _key_lock(key: str) -> Iterator[None]:
    with _key_locks_guard:
        lock = _key_locks[key]
    with lock:
        yield


def _audio_dir(project_id: int) -> Path:
    path = settings.project_dir(project_id) / "audio"
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_to_wav(raw: bytes, dest: Path, *, sample_rate: int = SAMPLE_RATE) -> float:
    """Every provider's output becomes the same mono 48k PCM WAV.

    Uniformity here is what makes concatenating 240 clips sample-accurate
    later, instead of a slow decode/re-encode per join.

    Temp names carry a unique token: two scenes with identical text hash to the
    same destination, and without this they race each other's scratch files.
    """
    token = uuid.uuid4().hex[:12]
    tmp_in = dest.with_name(f".{dest.stem}.{token}.in")
    tmp_out = dest.with_name(f".{dest.stem}.{token}.wav")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_in.write_bytes(raw)
    try:
        ffmpeg.run([
            "-i", str(tmp_in),
            "-ac", "1", "-ar", str(sample_rate),
            "-c:a", "pcm_s16le",
            str(tmp_out),
        ])
        tmp_out.replace(dest)   # atomic; a concurrent twin writing the same
                                # bytes to the same path is harmless
    finally:
        tmp_in.unlink(missing_ok=True)
        tmp_out.unlink(missing_ok=True)
    return ffmpeg.duration_of(dest)


def _with_retry(fn: Callable, *, attempts: int = 5, on_wait: Callable[[str], None] | None = None):
    """Rate limits are expected, not exceptional. Back off and say so."""
    delay = 2.0
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except TTSUnavailable:
            raise  # missing key or model: retrying cannot help
        except Exception as exc:
            last = exc
            if attempt == attempts - 1:
                break
            wait = delay + random.uniform(0, 0.75)
            if on_wait:
                on_wait(f"provider busy, retrying in {wait:.0f}s")
            time.sleep(wait)
            delay = min(delay * 2, 30.0)
    raise last if last else RuntimeError("retry failed")


def synthesize_one(
    *,
    project_id: int,
    scene_id: int,
    text: str,
    provider: TTSProvider,
    voice_id: str,
    opts: TTSOpts,
    meter: CostMeter | None = None,
    on_message: Callable[[str], None] | None = None,
) -> SceneAudio:
    key = audio_hash(text=text, provider=provider.name, voice_id=voice_id, opts=opts.cache_key())
    dest = _audio_dir(project_id) / f"{key}.wav"

    if dest.exists() and dest.stat().st_size > 1024:
        return SceneAudio(scene_id, dest, ffmpeg.duration_of(dest), cached=True, cost=0.0)

    # Repeated lines are common in narration ("No one spoke."). Serialising on
    # the cache key means identical text is synthesised once, not once per
    # occurrence - which on a paid provider is a duplicate charge.
    with _key_lock(key):
        if dest.exists() and dest.stat().st_size > 1024:
            return SceneAudio(scene_id, dest, ffmpeg.duration_of(dest), cached=True, cost=0.0)

        cost = provider.estimate_cost(text)
        if meter and cost:
            meter.reserve(cost)

        result = _with_retry(
            lambda: provider.synthesize(text, voice_id, opts),
            on_wait=on_message or (lambda _m: None),
        )
        duration = normalize_to_wav(result.audio, dest)
    return SceneAudio(scene_id, dest, duration, cached=False, cost=cost)


def synthesize_batch(
    *,
    project_id: int,
    items: list[tuple[int, str]],          # (scene_id, text)
    provider_name: str,
    voice_id: str,
    opts: TTSOpts | None = None,
    spend_ceiling: float | None = None,
    on_progress: Callable[[float, str], None] | None = None,
    should_stop: Callable[[], None] | None = None,
) -> dict[int, SceneAudio]:
    provider = get_provider(provider_name)
    usable, reason = provider.available()
    if not usable:
        raise TTSUnavailable(reason)

    opts = opts or TTSOpts(sample_rate=SAMPLE_RATE)
    meter = CostMeter(spend_ceiling)
    results: dict[int, SceneAudio] = {}
    workers = max(1, min(provider.max_concurrency, len(items) or 1))
    done = 0
    lock = threading.Lock()

    def work(item: tuple[int, str]) -> SceneAudio:
        scene_id, text = item
        if should_stop:
            should_stop()
        return synthesize_one(
            project_id=project_id, scene_id=scene_id, text=text,
            provider=provider, voice_id=voice_id, opts=opts, meter=meter,
            on_message=lambda m: on_progress(done / max(len(items), 1), m) if on_progress else None,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, item): item[0] for item in items}
        for future in as_completed(futures):
            audio = future.result()
            with lock:
                results[audio.scene_id] = audio
                done += 1
                if on_progress:
                    on_progress(
                        done / max(len(items), 1),
                        f"narrating scene {done}/{len(items)}"
                        + (f" - ${meter.spent:.2f}" if meter.spent else ""),
                    )
    log.info("synthesised %d scenes (%d cached), $%.2f", len(results),
             sum(1 for r in results.values() if r.cached), meter.spent)
    return results


def estimate_batch_cost(texts: list[str], provider_name: str) -> dict[str, float]:
    provider = get_provider(provider_name)
    total_bytes = sum(len(t.encode("utf-8")) for t in texts)
    return {
        "provider": provider.name,
        "bytes": float(total_bytes),
        "cost_usd": round(total_bytes / 1_000_000 * provider.cost_per_million_bytes, 4),
    }
