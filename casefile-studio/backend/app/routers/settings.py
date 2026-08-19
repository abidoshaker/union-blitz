"""API keys, provider status, environment health."""

from __future__ import annotations

import shutil
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import ffmpeg, keystore
from ..config import settings as app_settings
from ..providers import image as image_providers
from ..providers import video as video_providers
from ..providers import llm as llm_providers
from ..providers import tts as tts_providers
from ..security import redact
from ..services import align_service

router = APIRouter(prefix="/api/settings", tags=["settings"])

PROVIDER_INFO = {
    "fish": {"label": "Fish Audio", "what": "Narration voice, including your cloned voice",
             "cost": "$15 per 1M characters, about $0.78 per hour of video",
             "url": "https://fish.audio", "required": False},
    "anthropic": {"label": "Anthropic Claude", "what": "Splits your script into scenes",
                  "cost": "A few cents per script", "url": "https://console.anthropic.com",
                  "required": False},
    "openai": {"label": "OpenAI", "what": "Scene splitting, and optional AI images",
               "cost": "A few cents per script", "url": "https://platform.openai.com",
               "required": False},
    "pexels": {"label": "Pexels", "what": "Free stock photos", "cost": "Free",
               "url": "https://www.pexels.com/api/", "required": False},
    "pixabay": {"label": "Pixabay", "what": "Free stock photos", "cost": "Free",
                "url": "https://pixabay.com/api/docs/", "required": False},
    "nara": {"label": "US National Archives", "what": "Public-domain photos from DEA, FBI and other federal agencies",
             "cost": "Free", "url": "https://catalog.archives.gov/api-search", "required": False},
    "fal": {"label": "fal.ai", "what": "Flux AI images for B-roll",
            "cost": "About $0.03 per image", "url": "https://fal.ai", "required": False},
}


class KeyIn(BaseModel):
    provider: str
    value: str


@router.get("")
def get_settings() -> dict[str, Any]:
    configured = keystore.configured_providers()
    return {
        "providers": [
            {**info, "provider": name, "configured": name in configured}
            for name, info in PROVIDER_INFO.items()
        ],
        "tts": tts_providers.describe_all(),
        "images": image_providers.describe_all(),
        "video": video_providers.describe_all(),
        "llm": llm_providers.describe_all(),
        "align_methods": align_service.available_methods(),
        "defaults": {
            "width": app_settings.width, "height": app_settings.height,
            "fps": app_settings.fps, "crf": app_settings.crf,
            "preset": app_settings.preset, "encoder": app_settings.encoder,
            "render_workers": app_settings.render_workers,
            "loudness_lufs": app_settings.loudness_lufs,
            "words_per_minute": app_settings.words_per_minute,
        },
    }


@router.post("/keys")
def put_key(body: KeyIn) -> dict[str, Any]:
    if body.provider not in PROVIDER_INFO:
        raise HTTPException(400, f"Unknown provider {body.provider!r}")
    value = body.value.strip()
    if value:
        keystore.set_key(body.provider, value)
    else:
        keystore.delete_key(body.provider)
    # Provider objects cache availability decisions, so drop the registries.
    tts_providers._registry.cache_clear()
    image_providers._registry.cache_clear()
    llm_providers._registry.cache_clear()
    return {"provider": body.provider, "configured": bool(value), "redacted": redact(value)}


@router.post("/keys/{provider}/test")
def test_key(provider: str) -> dict[str, Any]:
    """Ping the provider so a wrong key is caught here, not mid-render."""
    key = keystore.get_key(provider)
    if not key:
        return {"ok": False, "message": "No key saved for this provider yet."}

    try:
        if provider == "fish":
            voices = tts_providers.get_provider("fish").list_voices()
            return {"ok": True, "message": f"Connected. {len(voices)} voices visible."}
        if provider in ("pexels", "pixabay"):
            results = image_providers.get_provider(provider).search("city night", count=3)
            return {"ok": True, "message": f"Connected. {len(results)} results for a test search."}
        if provider in ("anthropic", "openai"):
            reply = llm_providers.get_provider(provider).complete_json(
                'Reply with JSON only.', 'Return {"ok": true}', max_tokens=64,
            )
            return {"ok": bool(reply), "message": "Connected."}
        if provider == "fal":
            usable, reason = image_providers.get_provider("fal_flux").available()
            return {"ok": usable, "message": reason or "Key saved. It is charged per image, so it is not test-called."}
    except Exception as exc:
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "message": "Key saved."}


@router.get("/health")
def health() -> dict[str, Any]:
    caps = ffmpeg.capabilities(refresh=True)
    free = shutil.disk_usage(app_settings.data_dir).free
    kokoro_ok = tts_providers.get_provider("kokoro").available()
    return {
        "ffmpeg": {
            "found": caps.found, "path": caps.path,
            "libx264": caps.libx264, "libass": caps.libass,
            "hw_encoders": list(caps.hw_encoders),
            "problems": caps.problems(),
        },
        "disk_free_gb": round(free / 1e9, 1),
        "disk_warning": free < app_settings.min_free_disk_gb * 1e9,
        "cores": app_settings.render_workers,
        "align_method": align_service.best_method(),
        "kokoro": {"available": kokoro_ok[0], "reason": kokoro_ok[1]},
        "data_dir": str(app_settings.data_dir),
    }
