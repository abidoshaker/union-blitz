"""Silent timing track. No key, no model, no network.

Produces silence of the length the narration *would* be, estimated from word
count. That is enough to exercise the whole pipeline - scene durations,
alignment, subtitle placement, clip lengths, the concat - before committing to
a real voice. It is also what the test suite renders with.

Never usable for a real video, and marked as such everywhere.
"""

from __future__ import annotations

import io
import re
import wave

from ...config import settings
from .base import AudioResult, TTSOpts, TTSProvider, VoiceInfo

# A comma is a short beat, a full stop a longer one. Rough, but it makes draft
# timings land close enough to a real voice to be useful.
PAUSE_SEC = {",": 0.18, ";": 0.25, ":": 0.25, ".": 0.35, "!": 0.35, "?": 0.35, "-": 0.12}


def estimate_duration(text: str, wpm: int | None = None) -> float:
    words = len(re.findall(r"\b[\w']+\b", text))
    wpm = wpm or settings.words_per_minute
    base = words / max(wpm, 1) * 60.0
    pauses = sum(PAUSE_SEC.get(ch, 0.0) for ch in text)
    return max(0.6, base + pauses)


class DraftProvider(TTSProvider):
    name = "draft"
    label = "Draft timing track (silent)"
    supports_cloning = False
    is_local = True
    commercial_ok = False
    is_draft_only = True
    max_chars_per_request = 100_000
    max_concurrency = 8

    def available(self) -> tuple[bool, str]:
        return True, ""

    def list_voices(self) -> list[VoiceInfo]:
        return [
            VoiceInfo(
                id="silence", title="Silent timing track", provider=self.name,
                tags=["testing"], commercial_ok=False,
                note="Silence of the right length - for testing the pipeline, never for a real video",
            )
        ]

    def synthesize(self, text: str, voice_id: str, opts: TTSOpts) -> AudioResult:
        seconds = estimate_duration(text) / max(opts.speed, 0.1)
        rate = opts.sample_rate
        frames = int(seconds * rate)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(b"\x00\x00" * frames)
        return AudioResult(audio=buf.getvalue(), sample_rate=rate, duration=seconds)
