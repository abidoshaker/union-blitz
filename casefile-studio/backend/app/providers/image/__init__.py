"""Image provider registry."""

from __future__ import annotations

from functools import lru_cache

from .ai import FalFluxProvider, OpenAIImageProvider, PlaceholderProvider
from .base import AssetCandidate, ImageProvider, ImageUnavailable
from .stock import (
    InternetArchiveImageProvider, LibraryOfCongressProvider, NationalArchivesProvider,
    PexelsProvider, PixabayProvider, WikimediaProvider,
)

__all__ = [
    "AssetCandidate", "ImageProvider", "ImageUnavailable",
    "get_provider", "all_providers", "describe_all",
    "DEFAULT_STOCK", "DEFAULT_AI", "ARCHIVAL_PROVIDERS",
]

DEFAULT_STOCK = "pexels"
DEFAULT_AI = "placeholder"
# Where a scene flagged `depicts_real_person` is allowed to source from,
# most archival first.
# Tried in order for a scene with a real subject. US federal records first:
# they are public domain and most likely to be the actual event.
ARCHIVAL_PROVIDERS = (
    "nara", "loc", "wikimedia", "internet_archive_image", "pexels", "pixabay",
)


@lru_cache(maxsize=1)
def _registry() -> dict[str, ImageProvider]:
    providers: list[ImageProvider] = [
        PexelsProvider(), PixabayProvider(), WikimediaProvider(),
        InternetArchiveImageProvider(), LibraryOfCongressProvider(),
        NationalArchivesProvider(),
        OpenAIImageProvider(), FalFluxProvider(), PlaceholderProvider(),
    ]
    return {p.name: p for p in providers}


def get_provider(name: str | None) -> ImageProvider:
    provider = _registry().get((name or DEFAULT_STOCK).lower())
    if provider is None:
        raise ImageUnavailable(f"Unknown image provider {name!r}")
    return provider


def all_providers() -> list[ImageProvider]:
    return list(_registry().values())


def describe_all() -> list[dict]:
    """Every still library, tagged with whether it is a record or a mood.

    `is_archival` is the distinction the interface has to show: an archive may
    hold the actual event, a stock library never does. It lives here rather
    than on each adapter because it is a property of the ordering above, and
    two lists that can disagree is one list too many.
    """
    return [
        {**p.describe(), "is_archival": p.name in ARCHIVAL_PROVIDERS and p.kind != "ai"}
        for p in all_providers()
    ]
