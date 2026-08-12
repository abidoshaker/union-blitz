"""AI image generation for atmospheric and dramatisation B-roll.

Never for real named people - `real_person_guard` in image_service blocks that
route before any of these are reached.
"""

from __future__ import annotations

import base64

import httpx

from ...keystore import get_key
from .base import AssetCandidate, ImageProvider, ImageUnavailable

# Appended to every prompt so a project holds one look across 240 scenes.
STYLE_SUFFIX = (
    "cinematic true-crime documentary still, dark moody lighting, desaturated "
    "cold colour grade, 35mm film grain, shallow depth of field, no text, "
    "no watermark, no recognisable real person"
)
NEGATIVE = "text, watermark, logo, caption, deformed, cartoon, bright saturated colours, celebrity likeness"


class OpenAIImageProvider(ImageProvider):
    name = "openai_image"
    label = "OpenAI gpt-image"
    kind = "ai"
    cost_per_image = 0.04

    def available(self) -> tuple[bool, str]:
        return (True, "") if get_key("openai") else (False, "No OpenAI API key yet.")

    def generate(self, prompt: str, *, opts: dict | None = None) -> AssetCandidate:
        key = get_key("openai")
        if not key:
            raise ImageUnavailable("No OpenAI API key.")
        opts = opts or {}
        model = opts.get("model", "gpt-image-1")
        with httpx.Client(timeout=180) as client:
            resp = client.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "prompt": f"{prompt}. {STYLE_SUFFIX}",
                    "size": opts.get("size", "1536x1024"),
                    "n": 1,
                },
            )
            if resp.status_code >= 400:
                raise ImageUnavailable(f"OpenAI images: {resp.status_code} {resp.text[:200]}")
            data = resp.json()["data"][0]

        if data.get("b64_json"):
            return AssetCandidate(
                url="", provider=self.name, license="OpenAI generated - review OpenAI usage terms",
                attribution="AI-generated (OpenAI)", title=prompt[:120],
                extra={"bytes": base64.b64decode(data["b64_json"])},
            )
        return AssetCandidate(
            url=data.get("url", ""), provider=self.name,
            license="OpenAI generated - review OpenAI usage terms",
            attribution="AI-generated (OpenAI)", title=prompt[:120],
        )


class FalFluxProvider(ImageProvider):
    name = "fal_flux"
    label = "Flux via fal.ai"
    kind = "ai"
    cost_per_image = 0.025

    def available(self) -> tuple[bool, str]:
        return (True, "") if get_key("fal") else (False, "No fal.ai key yet.")

    def generate(self, prompt: str, *, opts: dict | None = None) -> AssetCandidate:
        key = get_key("fal")
        if not key:
            raise ImageUnavailable("No fal.ai API key.")
        opts = opts or {}
        model = opts.get("model", "fal-ai/flux/schnell")
        with httpx.Client(timeout=180) as client:
            resp = client.post(
                f"https://fal.run/{model}",
                headers={"Authorization": f"Key {key}"},
                json={
                    "prompt": f"{prompt}. {STYLE_SUFFIX}",
                    "image_size": opts.get("image_size", "landscape_16_9"),
                    "num_images": 1,
                    "enable_safety_checker": True,
                },
            )
            if resp.status_code >= 400:
                raise ImageUnavailable(f"fal.ai: {resp.status_code} {resp.text[:200]}")
            images = resp.json().get("images") or []
        if not images:
            raise ImageUnavailable("fal.ai returned no image.")
        img = images[0]
        return AssetCandidate(
            url=img.get("url", ""), provider=self.name,
            license="Flux via fal.ai - commercial use per fal terms",
            attribution="AI-generated (Flux)",
            width=int(img.get("width") or 0), height=int(img.get("height") or 0),
            title=prompt[:120],
        )


class PlaceholderProvider(ImageProvider):
    """Offline stand-in: a graded gradient card with the scene number.

    Lets the whole render pipeline be exercised, and a full hour storyboarded,
    with no keys and no network. Also what the test suite renders with.
    """

    name = "placeholder"
    label = "Placeholder card (offline)"
    kind = "ai"
    is_local = True
    cost_per_image = 0.0

    def available(self) -> tuple[bool, str]:
        try:
            import PIL  # noqa: F401
        except ImportError:
            return False, "Pillow is not installed."
        return True, ""

    def generate(self, prompt: str, *, opts: dict | None = None) -> AssetCandidate:
        import hashlib
        import io

        from PIL import Image, ImageDraw

        opts = opts or {}
        w, h = int(opts.get("width", 1920)), int(opts.get("height", 1080))
        seed = int(hashlib.sha256(prompt.encode()).hexdigest()[:8], 16)
        # Stay inside the app's palette: deep base, magenta/violet accents.
        top = (15 + seed % 25, 17 + seed // 7 % 20, 25 + seed // 13 % 35)
        bottom = (60 + seed // 3 % 60, 20 + seed // 11 % 30, 90 + seed // 5 % 70)

        img = Image.new("RGB", (w, h), top)
        draw = ImageDraw.Draw(img)
        for y in range(h):
            t = y / max(h - 1, 1)
            draw.line(
                [(0, y), (w, y)],
                fill=tuple(int(top[i] + (bottom[i] - top[i]) * (t ** 1.4)) for i in range(3)),
            )
        # A faint vignette so Ken Burns motion is actually visible on screen.
        for i in range(28):
            k = i / 28
            box = (int(w * 0.02 * k), int(h * 0.02 * k), int(w * (1 - 0.02 * k)), int(h * (1 - 0.02 * k)))
            draw.rectangle(box, outline=(0, 0, 0), width=6)

        label = (prompt or "scene")[:70]
        draw.text((int(w * 0.06), int(h * 0.82)), label, fill=(255, 176, 32))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        return AssetCandidate(
            url="", provider=self.name, license="Generated locally - placeholder, not for publication",
            attribution="Placeholder", width=w, height=h, title=label,
            extra={"bytes": buf.getvalue()},
        )
