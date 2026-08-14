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


# Best first. Fish is the paid narrator, Kokoro the free offline one that can
# still be published, Edge the free draft you must not monetise. Draft is
# silence and exists for tests, so it is never recommended to anyone.
PREFERENCE = ("fish", "kokoro", "edge")

#: What each provider should narrate with when nothing has been chosen yet.
DEFAULT_VOICE = {
    "fish": "",                       # Fish falls back to its own default model
    "kokoro": "am_michael",
    "edge": "en-US-GuyNeural",
    "draft": "silence",
}


def recommended() -> tuple[str, str]:
    """The best provider available right now, and a voice to start on.

    A project created with no narration settings would otherwise inherit the
    silent draft voice and render an hour of nothing.
    """
    for name in PREFERENCE:
        try:
            provider = get_provider(name)
        except TTSUnavailable:
            continue
        if provider.available()[0]:
            return name, DEFAULT_VOICE.get(name, "")
    return "draft", DEFAULT_VOICE["draft"]
