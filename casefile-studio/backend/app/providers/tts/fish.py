"""Fish Audio ("fishai") cloud TTS - the default narrator.

Two things this adapter is careful about:

* It speaks plain REST through httpx. The `fish-audio-sdk` PyPI package is a
  convenience only and the app must work without it, which is why the installer
  treats that package as optional. (The GitHub repo is fish-audio-python; the
  package is fish-audio-sdk. They get confused constantly.)
* Only the *cloud* API is commercially licensed. The open fish-speech weights
  are CC-BY-NC-SA-4.0 and cannot be used on a monetised channel, so local mode
  is gated behind an explicit flag and flagged in the UI.
"""

from __future__ import annotations

import io
import wave

import httpx

from ...keystore import get_key
from .base import AudioResult, TTSOpts, TTSProvider, TTSUnavailable, VoiceInfo

API_BASE = "https://api.fish.audio"
DEFAULT_MODEL = "s2.1-pro"
FREE_MODEL = "s2.1-pro-free"


class FishProvider(TTSProvider):
    name = "fish"
    label = "Fish Audio (cloud)"
    supports_cloning = True
    is_local = False
    commercial_ok = True
    max_chars_per_request = 8000
    # Starter tier allows 5 concurrent requests; 15 at $100 prepaid, 50 at $1000.
    max_concurrency = 5
    cost_per_million_bytes = 15.0

    def __init__(self, model: str = DEFAULT_MODEL, timeout: float = 180.0) -> None:
        self.model = model
        self.timeout = timeout

    # -- plumbing ---------------------------------------------------------
    def _key(self) -> str:
        key = get_key("fish")
        if not key:
            raise TTSUnavailable("No Fish Audio API key. Add one in Settings.")
        return key

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=API_BASE,
            timeout=self.timeout,
            headers={"Authorization": f"Bearer {self._key()}", "model": self.model},
        )

    def available(self) -> tuple[bool, str]:
        if not get_key("fish"):
            return False, "No Fish Audio API key yet - add one in Settings."
        return True, ""

    # -- voices -----------------------------------------------------------
    def list_voices(self) -> list[VoiceInfo]:
        voices: list[VoiceInfo] = []
        with self._client() as client:
            for params, is_own in (({"self": "true", "page_size": 100}, True),
                                   ({"page_size": 60, "sort_by": "score"}, False)):
                try:
                    resp = client.get("/model", params=params)
                    resp.raise_for_status()
                    items = resp.json().get("items", [])
                except (httpx.HTTPError, ValueError):
                    continue
                for item in items:
                    voices.append(
                        VoiceInfo(
                            id=str(item.get("_id") or item.get("id") or ""),
                            title=item.get("title") or "untitled",
                            provider=self.name,
                            tags=list(item.get("tags") or []),
                            is_clone=is_own,
                            commercial_ok=True,
                            note="Your model" if is_own else "Public model",
                        )
                    )
        # De-duplicate, keeping the user's own models first.
        seen: set[str] = set()
        unique = []
        for v in voices:
            if v.id and v.id not in seen:
                seen.add(v.id)
                unique.append(v)
        return unique

    def clone_voice(self, ref_audio: bytes, ref_text: str | None, title: str) -> str:
        data = {"title": title, "type": "tts", "train_mode": "fast"}
        if ref_text:
            data["texts"] = ref_text
        files = {"voices": ("reference.wav", ref_audio, "audio/wav")}
        with self._client() as client:
            resp = client.post("/model", data=data, files=files)
            resp.raise_for_status()
            body = resp.json()
        voice_id = str(body.get("_id") or body.get("id") or "")
        if not voice_id:
            raise TTSUnavailable(f"Fish did not return a voice id: {body}")
        return voice_id

    # -- synthesis --------------------------------------------------------
    def synthesize(self, text: str, voice_id: str, opts: TTSOpts) -> AudioResult:
        payload: dict[str, object] = {
            "text": text,
            "format": "wav",
            "normalize": True,
            "latency": "normal",
        }
        if voice_id:
            payload["reference_id"] = voice_id
        # Fish takes delivery as a prosody block. Sending it only when it
        # differs from neutral keeps identical requests byte-identical, which
        # is what lets their side cache and ours stay reproducible.
        if abs(opts.speed - 1.0) > 0.01:
            payload["prosody"] = {"speed": round(opts.speed, 3)}

        with self._client() as client:
            resp = client.post("/v1/tts", json=payload)
            if resp.status_code == 402:
                raise TTSUnavailable("Fish Audio rejected the request: out of credit.")
            if resp.status_code in (401, 403):
                raise TTSUnavailable("Fish Audio rejected the API key.")
            resp.raise_for_status()
            audio = resp.content

        return AudioResult(
            audio=audio,
            mime="audio/wav",
            sample_rate=_wav_rate(audio) or opts.sample_rate,
            duration=_wav_duration(audio),
        )


def _wav_rate(blob: bytes) -> int:
    try:
        with wave.open(io.BytesIO(blob)) as wf:
            return wf.getframerate()
    except (wave.Error, EOFError):
        return 0


def _wav_duration(blob: bytes) -> float:
    try:
        with wave.open(io.BytesIO(blob)) as wf:
            return wf.getnframes() / float(wf.getframerate() or 1)
    except (wave.Error, EOFError, ZeroDivisionError):
        return 0.0
