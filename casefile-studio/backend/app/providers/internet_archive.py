"""Shared Internet Archive plumbing, used by both the image and video adapters.

The Archive is the best free source of genuinely archival material for true
crime - period photographs, news footage, court records - but its licence
metadata is patchy and inconsistent. Everything here is built around that: an
item is only offered when there is positive evidence it is free to use, and the
evidence is carried through to the asset so the licence is recorded.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

import httpx

log = logging.getLogger("casefile.archive")

SEARCH_URL = "https://archive.org/advancedsearch.php"
METADATA_URL = "https://archive.org/metadata"
DOWNLOAD_URL = "https://archive.org/download"
THUMB_URL = "https://archive.org/services/img"

USER_AGENT = "CaseFileStudio/1.0 (+local video tool)"

# Collections that are wholesale public domain or openly licensed. Item-level
# licence metadata is missing far more often than not, so membership here is
# treated as evidence in its own right.
PD_COLLECTIONS = {
    "prelinger", "publicmoviescollection", "moviesandfilms", "newsandpublicaffairs",
    "fedflix", "nationalarchives", "usnationalarchives", "computerchronicles",
    "flickrcommons", "library_of_congress", "nasa", "usgs", "smithsonian",
    "metropolitanmuseumofart-gallery", "brooklynmuseum", "statelibraryofnsw",
    "bostonpubliclibrary", "nationalarchivesuk", "internetarchivebooks",
}
PD_LICENCE_HINTS = (
    "publicdomain", "creativecommons.org/publicdomain", "/cc0",
    "/by/", "/by-sa/", "/mark/", "usgovernment",
)


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def licence_of(doc: dict) -> str:
    return str(doc.get("licenseurl") or "").lower()


def collections_of(doc: dict) -> set[str]:
    return {c.lower() for c in as_list(doc.get("collection"))}


def is_free(doc: dict) -> bool:
    """Positive evidence that an item can be reused, not the absence of a ban."""
    licence = licence_of(doc)
    if any(hint in licence for hint in PD_LICENCE_HINTS):
        return True
    return bool(collections_of(doc) & PD_COLLECTIONS)


def licence_text(doc: dict) -> str:
    licence = doc.get("licenseurl")
    if licence:
        return str(licence)
    free = collections_of(doc) & PD_COLLECTIONS
    if free:
        return "Public-domain collection: " + ", ".join(sorted(free))
    return "Internet Archive - licence not stated, verify before publishing"


def attribution_of(doc: dict) -> str:
    identifier = doc.get("identifier", "")
    title = doc.get("title") or identifier
    creator = ", ".join(as_list(doc.get("creator"))[:2])
    who = f"{creator} - " if creator else ""
    return f"{who}{title} (Internet Archive: {identifier})"


def search_docs(
    query: str,
    *,
    mediatype: str,
    rows: int = 30,
    pd_only: bool = True,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Search items of one mediatype, keeping only ones we can actually use."""
    params = {
        "q": f"({query}) AND mediatype:({mediatype})",
        "fl[]": ["identifier", "title", "licenseurl", "collection", "year", "downloads", "creator"],
        "rows": str(max(rows, 1)),
        "page": "1",
        "output": "json",
        "sort[]": "downloads desc",
    }
    owns_client = client is None
    client = client or httpx.Client(timeout=45, headers={"User-Agent": USER_AGENT})
    try:
        resp = client.get(SEARCH_URL, params=params)
        resp.raise_for_status()
        docs = (resp.json().get("response") or {}).get("docs", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Internet Archive search failed for %r: %s", query, exc)
        return []
    finally:
        if owns_client:
            client.close()

    return [d for d in docs if not pd_only or is_free(d)]


def item_files(identifier: str, *, client: httpx.Client | None = None) -> list[dict]:
    owns_client = client is None
    client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    try:
        resp = client.get(f"{METADATA_URL}/{identifier}")
        resp.raise_for_status()
        return list(resp.json().get("files") or [])
    except (httpx.HTTPError, ValueError) as exc:
        log.debug("metadata fetch failed for %s: %s", identifier, exc)
        return []
    finally:
        if owns_client:
            client.close()


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
# Derivatives the Archive generates for its own UI. They are small and often
# letterboxed, so they make poor Ken Burns sources.
DERIVATIVE_MARKERS = ("_thumb", "_itemimage", "__ia_thumb", "_bw.", "_spectrogram")


def pick_image_file(files: Iterable[dict], *, min_bytes: int = 40_000,
                    max_bytes: int = 40_000_000) -> dict | None:
    """Largest usable original still from an item."""
    best: tuple[int, dict] | None = None
    for entry in files:
        name = str(entry.get("name", ""))
        lowered = name.lower()
        if not lowered.endswith(IMAGE_EXTS):
            continue
        if any(marker in lowered for marker in DERIVATIVE_MARKERS):
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size and not (min_bytes <= size <= max_bytes):
            continue
        # Prefer originals over derivatives, then prefer the biggest.
        rank = (1 if entry.get("source") == "original" else 0, size)
        if best is None or rank > (1 if best[1].get("source") == "original" else 0, best[0]):
            best = (size, entry)
    return best[1] if best else None


VIDEO_EXTS = (".mp4", ".m4v", ".webm", ".ogv")


def pick_video_file(files: Iterable[dict], *, max_bytes: int = 120_000_000) -> dict | None:
    """Smallest web-friendly derivative that is still a real encode."""
    best: tuple[tuple[int, int], dict] | None = None
    for entry in files:
        name = str(entry.get("name", "")).lower()
        if not name.endswith(VIDEO_EXTS):
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size and size > max_bytes:
            continue
        fmt = str(entry.get("format", "")).lower()
        friendly = 1 if ("512kb" in name or "h.264" in fmt or "mpeg4" in fmt) else 0
        rank = (friendly, -size)
        if best is None or rank > best[0]:
            best = (rank, entry)
    return best[1] if best else None


def download_url(identifier: str, name: str) -> str:
    return f"{DOWNLOAD_URL}/{identifier}/{name}"


def thumb_url(identifier: str) -> str:
    return f"{THUMB_URL}/{identifier}"
