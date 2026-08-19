"""Stock and archival video sources.

A deliberate limit: every provider here is a licensed API with a
machine-readable licence. There is no scraper for YouTube, news sites, or
anywhere else that would hand you footage you have no right to publish. For a
monetised channel that is the difference between B-roll and a copyright strike,
so the constraint is structural rather than a setting.

Roughly what each is for:
  Pexels / Pixabay      atmospheric B-roll - rain, night streets, police lights
  Internet Archive      genuine archival and public-domain footage
  Wikimedia Commons     documentary clips tied to a named subject
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import httpx

from ..keystore import get_key
from .image.base import AssetCandidate
from .internet_archive import (
    attribution_of, download_url, item_files, licence_text, pick_video_file,
    search_docs, thumb_url,
)

log = logging.getLogger("casefile.video")


class VideoUnavailable(RuntimeError):
    pass


class VideoProvider:
    name: str = "base"
    label: str = "Base"
    is_archival: bool = False
    requires_attribution: bool = False
    # Anything longer gets trimmed; anything larger is skipped outright, since
    # a 400 MB source for a 12-second scene is pure waste.
    max_bytes: int = 120_000_000

    def available(self) -> tuple[bool, str]:
        return True, ""

    def search(self, query: str, *, count: int = 10, opts: dict | None = None) -> list[AssetCandidate]:
        raise NotImplementedError

    def describe(self) -> dict[str, Any]:
        usable, reason = self.available()
        return {
            "name": self.name, "label": self.label, "kind": "video",
            "is_archival": self.is_archival,
            "requires_attribution": self.requires_attribution,
            "available": usable, "unavailable_reason": reason,
        }


def _pick_rendition(files: list[dict], max_width: int = 1920) -> dict | None:
    """Largest rendition that is still no wider than the output frame.

    Downloading 4K for a 1080p Ken Burns scene costs minutes of transfer and
    buys nothing.
    """
    usable = [f for f in files if f.get("width") and f.get("height")]
    if not usable:
        return files[0] if files else None
    within = [f for f in usable if f["width"] <= max_width]
    return max(within or usable, key=lambda f: f["width"])


class PexelsVideoProvider(VideoProvider):
    name = "pexels_video"
    label = "Pexels video"

    def available(self) -> tuple[bool, str]:
        return (True, "") if get_key("pexels") else (False, "No Pexels API key yet - it is free.")

    def search(self, query: str, *, count: int = 10, opts: dict | None = None) -> list[AssetCandidate]:
        key = get_key("pexels")
        if not key:
            raise VideoUnavailable("No Pexels API key.")
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                "https://api.pexels.com/videos/search",
                params={"query": query, "per_page": min(count, 80), "orientation": "landscape"},
                headers={"Authorization": key},
            )
            resp.raise_for_status()
            payload = resp.json()

        out: list[AssetCandidate] = []
        for item in payload.get("videos", []):
            rendition = _pick_rendition(
                [f for f in item.get("video_files", []) if f.get("file_type") == "video/mp4"]
            )
            if not rendition:
                continue
            out.append(AssetCandidate(
                url=rendition.get("link", ""),
                thumb=item.get("image", ""),
                license="Pexels License - free for commercial use, no attribution required",
                attribution=f"Video by {(item.get('user') or {}).get('name', 'unknown')} on Pexels",
                provider=self.name,
                width=int(rendition.get("width") or 0),
                height=int(rendition.get("height") or 0),
                title=item.get("url", "") or query,
                kind="video",
                duration=float(item.get("duration") or 0),
                has_audio=False,     # Pexels stock video is silent in practice
            ))
        return out


class PixabayVideoProvider(VideoProvider):
    name = "pixabay_video"
    label = "Pixabay video"

    def available(self) -> tuple[bool, str]:
        return (True, "") if get_key("pixabay") else (False, "No Pixabay API key yet - it is free.")

    def search(self, query: str, *, count: int = 10, opts: dict | None = None) -> list[AssetCandidate]:
        key = get_key("pixabay")
        if not key:
            raise VideoUnavailable("No Pixabay API key.")
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                "https://pixabay.com/api/videos/",
                params={"key": key, "q": query, "per_page": min(max(count, 3), 200), "safesearch": "true"},
            )
            resp.raise_for_status()
            payload = resp.json()

        out: list[AssetCandidate] = []
        for hit in payload.get("hits", []):
            streams = hit.get("videos") or {}
            rendition = _pick_rendition([
                {**v, "quality": name} for name, v in streams.items() if v.get("url")
            ])
            if not rendition:
                continue
            out.append(AssetCandidate(
                url=rendition.get("url", ""),
                thumb=rendition.get("thumbnail", ""),
                license="Pixabay Content License - free for commercial use, no attribution required",
                attribution=f"Video by {hit.get('user', 'unknown')} on Pixabay",
                provider=self.name,
                width=int(rendition.get("width") or 0),
                height=int(rendition.get("height") or 0),
                title=hit.get("tags") or query,
                kind="video",
                duration=float(hit.get("duration") or 0),
                has_audio=False,
            ))
        return out


class InternetArchiveVideoProvider(VideoProvider):
    """Archival footage. No key required.

    Shares its search, licence filtering and file selection with the image
    adapter (providers/internet_archive.py) so the two cannot drift apart.
    """

    name = "internet_archive"
    label = "Internet Archive (archival)"
    is_archival = True
    requires_attribution = True

    def search(self, query: str, *, count: int = 10, opts: dict | None = None) -> list[AssetCandidate]:
        opts = opts or {}
        with httpx.Client(timeout=45, headers={"User-Agent": "CaseFileStudio/1.0"}) as client:
            docs = search_docs(
                query, mediatype="movies", rows=min(count * 3, 60),
                pd_only=opts.get("public_domain_only", True), client=client,
            )
            out: list[AssetCandidate] = []
            for doc in docs:
                if len(out) >= count:
                    break
                identifier = doc.get("identifier")
                if not identifier:
                    continue
                chosen = pick_video_file(
                    item_files(identifier, client=client), max_bytes=self.max_bytes
                )
                if not chosen:
                    continue
                out.append(AssetCandidate(
                    url=download_url(identifier, chosen["name"]),
                    thumb=thumb_url(identifier),
                    license=licence_text(doc),
                    attribution=attribution_of(doc),
                    provider=self.name,
                    width=int(chosen.get("width") or 0),
                    height=int(chosen.get("height") or 0),
                    title=str(doc.get("title") or identifier),
                    kind="video",
                    has_audio=True,   # archival footage usually carries a soundtrack
                ))
        return out


@lru_cache(maxsize=1)
def _registry() -> dict[str, VideoProvider]:
    providers: list[VideoProvider] = [
        PexelsVideoProvider(), PixabayVideoProvider(), InternetArchiveVideoProvider(),
    ]
    return {p.name: p for p in providers}


def get_provider(name: str | None) -> VideoProvider:
    provider = _registry().get((name or "pexels_video").lower())
    if provider is None:
        raise VideoUnavailable(f"Unknown video provider {name!r}")
    return provider


def all_providers() -> list[VideoProvider]:
    return list(_registry().values())


def describe_all() -> list[dict]:
    return [p.describe() for p in all_providers()]
