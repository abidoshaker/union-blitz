"""Sourcing and preparing the stills.

Carries the real-person guard, and the chapter image pool that keeps an
hour-long video from costing $10 in generated images.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import settings
from ..providers.image import (
    ARCHIVAL_PROVIDERS,
    AssetCandidate,
    ImageUnavailable,
    get_provider,
)

log = logging.getLogger("casefile.image")


class RealPersonBlocked(RuntimeError):
    """AI generation was attempted on a scene flagged as a real individual."""


@dataclass
class StoredImage:
    path: Path
    provider: str
    license: str
    attribution: str
    width: int
    height: int
    source_url: str
    content_hash: str


def _assets_dir(project_id: int) -> Path:
    path = settings.project_dir(project_id) / "assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def real_person_guard(*, depicts_real_person: bool, provider_name: str) -> None:
    """Spec 6: AI generation is blocked on scenes flagged as a real individual.

    Enforced in the service layer rather than only in the UI, so a batch
    operation or a direct API call cannot route around it.
    """
    if not depicts_real_person:
        return
    provider = get_provider(provider_name)
    if provider.kind == "ai" and provider.name not in ARCHIVAL_PROVIDERS:
        raise RealPersonBlocked(
            "This scene is flagged as depicting a real person, so AI image "
            "generation is blocked. Use archival or licensed photography "
            f"({', '.join(ARCHIVAL_PROVIDERS)}) instead. Generating a likeness "
            "of a real individual carries right-of-publicity risk and breaches "
            "most image APIs' terms."
        )


def _download(url: str) -> bytes:
    with httpx.Client(timeout=120, follow_redirects=True,
                      headers={"User-Agent": "CaseFileStudio/1.0"}) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def _notify_unsplash_download(candidate: AssetCandidate) -> None:
    """Unsplash's API terms require this ping when an image is actually used."""
    if candidate.provider != "unsplash" or not candidate.download_location:
        return
    from ..keystore import get_key

    key = get_key("unsplash")
    if not key:
        return
    try:
        with httpx.Client(timeout=15) as client:
            client.get(candidate.download_location, headers={"Authorization": f"Client-ID {key}"})
    except httpx.HTTPError as exc:
        log.warning("Unsplash download ping failed: %s", exc)


def store_candidate(project_id: int, candidate: AssetCandidate) -> StoredImage:
    """Fetch (or take inline bytes), normalise, and cache by content hash."""
    blob = candidate.extra.get("bytes")
    if blob is None:
        if not candidate.url:
            raise ImageUnavailable("Image candidate has neither bytes nor a URL.")
        blob = _download(candidate.url)
        _notify_unsplash_download(candidate)

    digest = hashlib.sha256(blob).hexdigest()[:32]
    dest = _assets_dir(project_id) / f"{digest}.jpg"
    if not dest.exists():
        _write_normalised(blob, dest)

    width, height = _dimensions(dest)
    return StoredImage(
        path=dest, provider=candidate.provider, license=candidate.license,
        attribution=candidate.attribution, width=width, height=height,
        source_url=candidate.url, content_hash=digest,
    )


def _write_normalised(blob: bytes, dest: Path) -> None:
    """Baseline JPEG, RGB, at least the render size.

    Ken Burns crops into the image, so anything smaller than the output frame
    would go soft as soon as it zooms.
    """
    from io import BytesIO

    from PIL import Image, ImageOps

    with Image.open(BytesIO(blob)) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        min_w = int(settings.width * settings.kenburns_upscale)
        if img.width < min_w:
            scale = min_w / img.width
            img = img.resize((min_w, max(1, int(img.height * scale))), Image.LANCZOS)
        # A very large source costs zoompan dearly for no visible gain.
        max_w = int(settings.width * settings.kenburns_upscale * 1.5)
        if img.width > max_w:
            scale = max_w / img.width
            img = img.resize((max_w, max(1, int(img.height * scale))), Image.LANCZOS)
        img.save(dest, format="JPEG", quality=90, optimize=True)


def _dimensions(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return img.width, img.height
    except Exception:
        return 0, 0


def source_image(
    *,
    project_id: int,
    prompt: str,
    provider_name: str,
    depicts_real_person: bool = False,
    search_query: str | None = None,
    index: int = 0,
) -> StoredImage:
    """One image for one scene, by whichever route the provider supports."""
    real_person_guard(depicts_real_person=depicts_real_person, provider_name=provider_name)
    provider = get_provider(provider_name)
    usable, reason = provider.available()
    if not usable:
        raise ImageUnavailable(reason)

    if provider.kind == "ai":
        candidate = provider.generate(prompt)
    else:
        query = search_query or _search_terms(prompt)
        results = provider.search(query, count=12)
        if not results:
            raise ImageUnavailable(f"{provider.label} returned nothing for {query!r}.")
        candidate = results[index % len(results)]
    return store_candidate(project_id, candidate)


def build_chapter_pool(
    *,
    project_id: int,
    prompts: list[str],
    provider_name: str,
    pool_size: int,
    depicts_real_person: bool = False,
) -> list[StoredImage]:
    """Spec 6: N images per chapter, reused across that chapter's scenes.

    240 unique AI images costs about $10 a video; 8 per chapter costs well
    under a dollar and, with a different Ken Burns move per scene, does not
    read as repetition.
    """
    real_person_guard(depicts_real_person=depicts_real_person, provider_name=provider_name)
    picks = _spread(prompts, pool_size)
    pool: list[StoredImage] = []
    for i, prompt in enumerate(picks):
        try:
            pool.append(source_image(
                project_id=project_id, prompt=prompt, provider_name=provider_name,
                depicts_real_person=depicts_real_person, index=i,
            ))
        except Exception as exc:
            log.warning("pool image %d failed: %s", i, exc)
    if not pool:
        raise ImageUnavailable("Could not source any images for this chapter.")
    return pool


def _spread(items: list[str], n: int) -> list[str]:
    """Evenly sample n prompts across a chapter so the pool covers its range."""
    if not items:
        return []
    n = max(1, min(n, len(items)))
    step = len(items) / n
    return [items[min(len(items) - 1, int(i * step))] for i in range(n)]


def _search_terms(prompt: str) -> str:
    """Stock search engines want 2-4 plain words, not a full image prompt."""
    head = prompt.split(" - ")[0]
    words = [w.strip(",.").lower() for w in head.split() if len(w) > 3]
    return " ".join(words[:4]) or "dark city night"
