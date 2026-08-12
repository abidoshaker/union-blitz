"""TTS adapter interface. Section 5 of BUILD_SPEC.md."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VoiceInfo:
    id: str
    title: str
    provider: str
    tags: list[str] = field(default_factory=list)
    is_clone: bool = False
    commercial_ok: bool = True
    preview_url: str = ""
    note: str = ""


@dataclass
class TTSOpts:
    fmt: str = "wav"
    speed: float = 1.0
    sample_rate: int = 48000
    extra: dict[str, Any] = field(default_factory=dict)

    def cache_key(self) -> dict[str, Any]:
        return {"fmt": self.fmt, "speed": self.speed, "sr": self.sample_rate, **self.extra}


@dataclass
class AudioResult:
    audio: bytes
    mime: str = "audio/wav"
    sample_rate: int = 48000
    duration: float = 0.0


class TTSUnavailable(RuntimeError):
    """Provider is not usable right now: missing key, missing model, offline."""


class TTSProvider:
    name: str = "base"
    label: str = "Base"
    supports_cloning: bool = False
    is_local: bool = False
    commercial_ok: bool = True
    is_draft_only: bool = False
    max_chars_per_request: int = 4000
    max_concurrency: int = 4
    cost_per_million_bytes: float = 0.0

    def available(self) -> tuple[bool, str]:
        """(usable, human-readable reason if not)."""
        return True, ""

    def list_voices(self) -> list[VoiceInfo]:
        raise NotImplementedError

    def clone_voice(self, ref_audio: bytes, ref_text: str | None, title: str) -> str:
        raise NotImplementedError(f"{self.name} cannot clone voices")

    def synthesize(self, text: str, voice_id: str, opts: TTSOpts) -> AudioResult:
        raise NotImplementedError

    def estimate_cost(self, text: str) -> float:
        if not self.cost_per_million_bytes:
            return 0.0
        return len(text.encode("utf-8")) / 1_000_000 * self.cost_per_million_bytes

    def describe(self) -> dict[str, Any]:
        usable, reason = self.available()
        return {
            "name": self.name,
            "label": self.label,
            "supports_cloning": self.supports_cloning,
            "is_local": self.is_local,
            "commercial_ok": self.commercial_ok,
            "is_draft_only": self.is_draft_only,
            "max_concurrency": self.max_concurrency,
            "cost_per_million_bytes": self.cost_per_million_bytes,
            "available": usable,
            "unavailable_reason": reason,
        }
