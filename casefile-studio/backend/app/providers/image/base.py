"""Image adapter interface. Section 6 of BUILD_SPEC.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AssetCandidate:
    url: str
    thumb: str = ""
    license: str = ""
    attribution: str = ""
    provider: str = ""
    width: int = 0
    height: int = 0
    title: str = ""
    # 'image' or 'video'. Video candidates also carry a duration and a poster
    # frame, which the renderer uses for the crossfade into the clip.
    kind: str = "image"
    duration: float = 0.0
    has_audio: bool = False
    # Unsplash's API terms require pinging a download endpoint when an image is
    # actually used. Carried here so image_service can honour it.
    download_location: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class ImageUnavailable(RuntimeError):
    pass


class ImageProvider:
    name: str = "base"
    label: str = "Base"
    kind: str = "stock"           # 'stock' | 'ai'
    is_local: bool = False
    cost_per_image: float = 0.0
    requires_attribution: bool = False

    def available(self) -> tuple[bool, str]:
        return True, ""

    def search(self, query: str, *, count: int = 12, opts: dict | None = None) -> list[AssetCandidate]:
        raise NotImplementedError(f"{self.name} cannot search")

    def generate(self, prompt: str, *, opts: dict | None = None) -> AssetCandidate:
        raise NotImplementedError(f"{self.name} cannot generate")

    def describe(self) -> dict[str, Any]:
        usable, reason = self.available()
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "is_local": self.is_local,
            "cost_per_image": self.cost_per_image,
            "requires_attribution": self.requires_attribution,
            "available": usable,
            "unavailable_reason": reason,
        }
