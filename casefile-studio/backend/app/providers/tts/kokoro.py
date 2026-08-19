"""Kokoro-82M local TTS. Apache-2.0, CPU-fast, no cloning.

The free, commercially-safe option. Use it to hear a full hour before spending
anything on Fish Audio.
"""

from __future__ import annotations

import io
import wave

import numpy as np

from ...config import settings
from .base import AudioResult, TTSOpts, TTSProvider, TTSUnavailable, VoiceInfo

# The 54 shipped voices; a representative, labelled subset is surfaced first.
FEATURED = [
    ("am_michael", "Michael (US male)", ["narration", "documentary"]),
    ("am_adam", "Adam (US male)", ["deep"]),
    ("am_onyx", "Onyx (US male)", ["deep", "grave"]),
    ("bm_george", "George (UK male)", ["documentary"]),
    ("bm_lewis", "Lewis (UK male)", ["gravelly"]),
    ("af_heart", "Heart (US female)", ["warm"]),
    ("af_bella", "Bella (US female)", ["narration"]),
    ("bf_emma", "Emma (UK female)", ["measured"]),
]


class KokoroProvider(TTSProvider):
    name = "kokoro"
    label = "Kokoro-82M (local, free)"
    supports_cloning = False
    is_local = True
    commercial_ok = True
    max_chars_per_request = 2000
    cost_per_million_bytes = 0.0

    def __init__(self) -> None:
        self._engine = None
        self.max_concurrency = max(1, (settings.render_workers or 1))

    # -- availability -----------------------------------------------------
    def _paths(self) -> tuple:
        return (settings.models_dir / "kokoro-v1.0.onnx", settings.models_dir / "voices-v1.0.bin")

    def available(self) -> tuple[bool, str]:
        model, voices = self._paths()
        if not model.exists() or not voices.exists():
            return False, "Kokoro model files are missing. Run tools/fetch_models.py."
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            return False, "kokoro-onnx is not installed. Re-run install-deps.bat."
        return True, ""

    def _load(self):
        if self._engine is not None:
            return self._engine
        usable, reason = self.available()
        if not usable:
            raise TTSUnavailable(reason)
        from kokoro_onnx import Kokoro

        model, voices = self._paths()
        self._engine = Kokoro(str(model), str(voices))
        return self._engine

    # -- voices -----------------------------------------------------------
    def list_voices(self) -> list[VoiceInfo]:
        """The curated eight straight away; the other forty-odd if they are free.

        Loading the ONNX model to enumerate voice names takes several seconds,
        and the voice picker opens on every visit to the storyboard. Waiting
        for the model just to draw a list is the wrong trade: show the featured
        voices at once, and fill in the rest only once something else has
        already paid to load the engine.
        """
        featured = [
            VoiceInfo(id=vid, title=title, provider=self.name, tags=tags,
                      commercial_ok=True, note="Apache-2.0, runs offline")
            for vid, title, tags in FEATURED
        ]
        if self._engine is None:
            return featured
        try:
            names = sorted(getattr(self._engine, "get_voices", lambda: [])())
        except Exception:
            return featured
        known = {v.id for v in featured}
        featured += [
            VoiceInfo(id=n, title=n, provider=self.name, commercial_ok=True,
                      note="Apache-2.0, runs offline")
            for n in names if n not in known
        ]
        return featured

    # -- synthesis --------------------------------------------------------
    def synthesize(self, text: str, voice_id: str, opts: TTSOpts) -> AudioResult:
        engine = self._load()
        samples, rate = engine.create(
            text, voice=voice_id or "am_michael", speed=opts.speed, lang="en-us"
        )
        samples = np.asarray(samples, dtype=np.float32)
        pcm = np.clip(samples, -1.0, 1.0)
        blob = _to_wav(pcm, rate)
        return AudioResult(audio=blob, sample_rate=rate, duration=len(pcm) / float(rate or 1))


def _to_wav(samples: "np.ndarray", rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((samples * 32767.0).astype("<i2").tobytes())
    return buf.getvalue()
