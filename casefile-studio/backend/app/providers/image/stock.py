"""Free stock and archival sources.

Every candidate carries a machine-readable license and attribution string,
because for true crime the provenance of a photo is the thing that keeps you
out of trouble later.
"""

from __future__ import annotations

import httpx

from ...keystore import get_key
from ..internet_archive import (
    attribution_of, download_url, item_files, licence_text, pick_image_file,
    search_docs, thumb_url,
)
from .base import AssetCandidate, ImageProvider, ImageUnavailable


class PexelsProvider(ImageProvider):
    name = "pexels"
    label = "Pexels"
    kind = "stock"

    def available(self) -> tuple[bool, str]:
        return (True, "") if get_key("pexels") else (False, "No Pexels API key yet - it is free to get one.")

    def search(self, query: str, *, count: int = 12, opts: dict | None = None) -> list[AssetCandidate]:
        key = get_key("pexels")
        if not key:
            raise ImageUnavailable("No Pexels API key.")
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                "https://api.pexels.com/v1/search",
                params={"query": query, "per_page": min(count, 80), "orientation": "landscape"},
                headers={"Authorization": key},
            )
            resp.raise_for_status()
            payload = resp.json()

        out = []
        for photo in payload.get("photos", []):
            src = photo.get("src", {})
            out.append(AssetCandidate(
                url=src.get("original") or src.get("large2x") or "",
                thumb=src.get("medium", ""),
                license="Pexels License - free for commercial use, no attribution required",
                attribution=f"Photo by {photo.get('photographer', 'unknown')} on Pexels",
                provider=self.name,
                width=int(photo.get("width") or 0),
                height=int(photo.get("height") or 0),
                title=photo.get("alt") or query,
            ))
        return out


class PixabayProvider(ImageProvider):
    name = "pixabay"
    label = "Pixabay"
    kind = "stock"

    def available(self) -> tuple[bool, str]:
        return (True, "") if get_key("pixabay") else (False, "No Pixabay API key yet - it is free to get one.")

    def search(self, query: str, *, count: int = 12, opts: dict | None = None) -> list[AssetCandidate]:
        key = get_key("pixabay")
        if not key:
            raise ImageUnavailable("No Pixabay API key.")
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                "https://pixabay.com/api/",
                params={"key": key, "q": query, "image_type": "photo",
                        "orientation": "horizontal", "per_page": min(max(count, 3), 200), "safesearch": "true"},
            )
            resp.raise_for_status()
            payload = resp.json()

        return [
            AssetCandidate(
                url=hit.get("largeImageURL") or hit.get("webformatURL") or "",
                thumb=hit.get("previewURL", ""),
                license="Pixabay Content License - free for commercial use, no attribution required",
                attribution=f"Image by {hit.get('user', 'unknown')} on Pixabay",
                provider=self.name,
                width=int(hit.get("imageWidth") or 0),
                height=int(hit.get("imageHeight") or 0),
                title=hit.get("tags") or query,
            )
            for hit in payload.get("hits", [])
        ]


class WikimediaProvider(ImageProvider):
    """No API key. The best source for public-domain archival photos of real people."""

    name = "wikimedia"
    label = "Wikimedia Commons"
    kind = "stock"
    requires_attribution = True

    def available(self) -> tuple[bool, str]:
        return True, ""

    def search(self, query: str, *, count: int = 12, opts: dict | None = None) -> list[AssetCandidate]:
        params = {
            "action": "query", "format": "json", "generator": "search",
            "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": "6",
            "gsrlimit": str(min(count, 50)),
            "prop": "imageinfo", "iiprop": "url|size|extmetadata",
            "iiurlwidth": "480",
        }
        with httpx.Client(timeout=30, headers={"User-Agent": "CaseFileStudio/1.0"}) as client:
            resp = client.get("https://commons.wikimedia.org/w/api.php", params=params)
            resp.raise_for_status()
            payload = resp.json()

        out = []
        for page in (payload.get("query", {}).get("pages", {}) or {}).values():
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {}) or {}
            out.append(AssetCandidate(
                url=info.get("url", ""),
                thumb=info.get("thumburl", ""),
                license=_plain(meta.get("LicenseShortName", {}).get("value", "see Commons")),
                attribution=_plain(meta.get("Artist", {}).get("value", "Wikimedia Commons")),
                provider=self.name,
                width=int(info.get("width") or 0),
                height=int(info.get("height") or 0),
                title=page.get("title", ""),
            ))
        return [c for c in out if c.url]


def _plain(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", html or "").strip()[:300]


class InternetArchiveImageProvider(ImageProvider):
    """Archival stills from the Internet Archive. No API key.

    The best free source of period photographs for true crime - and previously
    missing entirely: the Archive was wired up as a video source only, so
    selecting it for images silently returned nothing.

    Licence metadata on the Archive is patchy, so `public_domain_only` is on by
    default and items without positive evidence of a free licence are dropped
    rather than surfaced with a vague warning.
    """

    name = "internet_archive_image"
    label = "Internet Archive (archival stills)"
    kind = "stock"
    requires_attribution = True

    def available(self) -> tuple[bool, str]:
        return True, ""

    def search(self, query: str, *, count: int = 12, opts: dict | None = None) -> list[AssetCandidate]:
        opts = opts or {}
        pd_only = opts.get("public_domain_only", True)

        with httpx.Client(timeout=45, headers={"User-Agent": "CaseFileStudio/1.0"}) as client:
            docs = search_docs(
                query, mediatype="image", rows=min(count * 3, 60),
                pd_only=pd_only, client=client,
            )
            out: list[AssetCandidate] = []
            for doc in docs:
                if len(out) >= count:
                    break
                identifier = doc.get("identifier")
                if not identifier:
                    continue
                chosen = pick_image_file(item_files(identifier, client=client))
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
                    kind="image",
                ))
        return out
