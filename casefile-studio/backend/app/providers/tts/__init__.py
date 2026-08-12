"""TTS provider registry."""

from __future__ import annotations

from functools import lru_cache

from .base import AudioResult, TTSOpts, TTSProvider, TTSUnavailable, VoiceInfo
from .draft import DraftProvider
from .edge import EdgeProvider
from .fish import FishProvider
from .kokoro import KokoroProvider

__all__ = [
    "AudioResult", "TTSOpts", "TTSProvider", "TTSUnavailable", "VoiceInfo",
    "get_provider", "all_providers", "describe_all", "DEFAULT_PROVIDER",
]

DEFAULT_PROVIDER = "fish"


@lru_cache(maxsize=1)
def _registry() -> dict[str, TTSProvider]:
    providers: list[TTSProvider] = [
        FishProvider(),
        KokoroProvider(),
        EdgeProvider(),
        DraftProvider(),
    ]
    return {p.name: p for p in providers}


def get_provider(name: str | None) -> TTSProvider:
    registry = _registry()
    provider = registry.get((name or DEFAULT_PROVIDER).lower())
    if provider is None:
        raise TTSUnavailable(f"Unknown TTS provider {name!r}")
    return provider


def all_providers() -> list[TTSProvider]:
    return list(_registry().values())


def describe_all() -> list[dict]:
    return [p.describe() for p in all_providers()]
