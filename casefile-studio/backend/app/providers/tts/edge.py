"""Edge-TTS: free, no API key, needs a network connection.

The cheapest way to listen to a full hour-long draft before paying Fish for the
final. Microsoft's terms make this unsuitable as a monetised channel's final
narration, so it is marked draft-only.
"""

from __future__ import annotations

import asyncio
import io
import wave

from .base import AudioResult, TTSOpts, TTSProvider, TTSUnavailable, VoiceInfo

FEATURED = [
    ("en-US-GuyNeural", "Guy (US male)", ["narration"]),
    ("en-US-ChristopherNeural", "Christopher (US male)", ["documentary", "deep"]),
    ("en-US-EricNeural", "Eric (US male)", ["measured"]),
    ("en-GB-RyanNeural", "Ryan (UK male)", ["documentary"]),
    ("en-US-JennyNeural", "Jenny (US female)", ["warm"]),
    ("en-GB-SoniaNeural", "Sonia (UK female)", ["measured"]),
]


class EdgeProvider(TTSProvider):
    name = "edge"
    label = "Edge TTS (free, draft only)"
    supports_cloning = False
    is_local = False
    commercial_ok = False
    is_draft_only = True
    max_chars_per_request = 3000
    max_concurrency = 3

    def available(self) -> tuple[bool, str]:
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return False, "edge-tts is not installed. Re-run install-deps.bat."
        return True, ""

    def list_voices(self) -> list[VoiceInfo]:
        return [
            VoiceInfo(id=vid, title=title, provider=self.name, tags=tags,
                      commercial_ok=False, note="Free, drafting only - not for a monetised final")
            for vid, title, tags in FEATURED
        ]

    def synthesize(self, text: str, voice_id: str, opts: TTSOpts) -> AudioResult:
        usable, reason = self.available()
        if not usable:
            raise TTSUnavailable(reason)
        import edge_tts

        # Edge wants a percentage off neutral, signed, as a string.
        delta = int(round((opts.speed - 1.0) * 100))
        rate = f"{delta:+d}%"

        async def _run() -> bytes:
            chunks = bytearray()
            comm = edge_tts.Communicate(text, voice_id or "en-US-GuyNeural", rate=rate)
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    chunks.extend(chunk["data"])
            return bytes(chunks)

        try:
            mp3 = asyncio.run(_run())
        except Exception as exc:
            raise TTSUnavailable(f"Edge TTS failed: {exc}") from exc
        if not mp3:
            raise TTSUnavailable("Edge TTS returned no audio.")
        return AudioResult(audio=mp3, mime="audio/mpeg", duration=0.0)
