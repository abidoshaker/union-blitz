"""Image provider registry."""

from __future__ import annotations

from functools import lru_cache

from .ai import FalFluxProvider, OpenAIImageProvider, PlaceholderProvider
from .base import AssetCandidate, ImageProvider, ImageUnavailable
from .stock import PexelsProvider, PixabayProvider, WikimediaProvider

__all__ = [
    "AssetCandidate", "ImageProvider", "ImageUnavailable",
    "get_provider", "all_providers", "describe_all",
    "DEFAULT_STOCK", "DEFAULT_AI", "ARCHIVAL_PROVIDERS",
]

DEFAULT_STOCK = "pexels"
DEFAULT_AI = "placeholder"
# Where a scene flagged `depicts_real_person` is allowed to source from.
ARCHIVAL_PROVIDERS = ("wikimedia", "pexels", "pixabay")


@lru_cache(maxsize=1)
def _registry() -> dict[str, ImageProvider]:
    providers: list[ImageProvider] = [
        PexelsProvider(), PixabayProvider(), WikimediaProvider(),
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
    return [p.describe() for p in all_providers()]
