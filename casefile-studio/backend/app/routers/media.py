"""Voices, assets, uploads, file serving."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..models import Asset, RenderOutput, Scene, Voice
from ..providers import image as image_providers
from ..providers import tts as tts_providers
from ..services import image_service, speech
from ..services.tts_service import normalize_to_wav

router = APIRouter(prefix="/api", tags=["media"])


# ---------------------------------------------------------------------------
# Voices
# ---------------------------------------------------------------------------

@router.get("/voices")
def list_voices(provider: str | None = None) -> dict[str, Any]:
    """Every voice on offer, plus what each provider is and is not good for.

    `providers` carries the facts a choice actually turns on - whether it can
    be used on a monetised channel, what it costs, whether it runs offline -
    so the interface never has to hard-code a list that drifts from the
    adapters.
    """
    out: list[dict] = []
    errors: list[dict] = []
    described: list[dict] = []
    names = [provider] if provider else [p.name for p in tts_providers.all_providers()]
    for name in names:
        adapter = tts_providers.get_provider(name)
        info = adapter.describe()
        described.append(info)
        usable, reason = adapter.available()
        if not usable:
            errors.append({"provider": name, "reason": reason})
            continue
        try:
            for voice in adapter.list_voices():
                out.append({
                    "id": voice.id, "title": voice.title, "provider": voice.provider,
                    "tags": voice.tags, "is_clone": voice.is_clone,
                    "commercial_ok": voice.commercial_ok, "note": voice.note,
                })
        except Exception as exc:
            errors.append({"provider": name, "reason": str(exc)})
    return {"voices": out, "unavailable": errors, "providers": described}


@router.post("/voices/clone")
async def clone_voice(
    title: str = Form(...),
    provider: str = Form("fish"),
    reference_text: str = Form(""),
    sample: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    adapter = tts_providers.get_provider(provider)
    if not adapter.supports_cloning:
        raise HTTPException(400, f"{adapter.label} cannot clone voices.")
    blob = await sample.read()
    if len(blob) < 8000:
        raise HTTPException(400, "That reference clip is too short. Aim for 10-15 seconds of clean speech.")

    try:
        voice_id = adapter.clone_voice(blob, reference_text or None, title)
    except Exception as exc:
        raise HTTPException(502, f"Cloning failed: {exc}") from exc

    ref_dir = settings.data_dir / "voices"
    ref_dir.mkdir(parents=True, exist_ok=True)
    ref_path = ref_dir / f"{voice_id}.wav"
    try:
        normalize_to_wav(blob, ref_path)
    except Exception:
        ref_path = Path("")

    voice = Voice(
        provider=provider, external_voice_id=voice_id, title=title, is_clone=True,
        reference_audio_path=str(ref_path), commercial_ok=adapter.commercial_ok,
        license_note="You must hold the rights to the voice in the reference clip.",
    )
    session.add(voice)
    session.commit()
    return {"voice_id": voice_id, "title": title, "provider": provider}


class PreviewIn(BaseModel):
    provider: str = "draft"
    voice_id: str = ""
    text: str = "In the winter of 1974, the ledger was still open on the desk."
    speed: float = 1.0
    spoken_numbers: bool = True


@router.post("/voices/preview")
def preview_voice(body: PreviewIn) -> FileResponse:
    """Hear a voice on a line before committing an hour of narration to it.

    The preview goes through the same spoken-form rewrite and the same trim as
    the real thing, so what you audition is what you get - auditioning a
    prettier version of the pipeline would be worse than not auditioning.
    """
    adapter = tts_providers.get_provider(body.provider)
    usable, reason = adapter.available()
    if not usable:
        raise HTTPException(400, reason)

    opts = tts_providers.TTSOpts(
        speed=max(0.5, min(2.0, body.speed)),
        spoken_form=body.spoken_numbers,
    )
    line = body.text[:400]
    said = speech.to_spoken(line) if opts.spoken_form else line
    try:
        result = adapter.synthesize(said, body.voice_id, opts)
    except Exception as exc:
        raise HTTPException(502, f"Preview failed: {exc}") from exc

    preview_dir = settings.data_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)
    # The voice id can be a path-unsafe model id, and two voices must never
    # collide on one file or you audition the wrong one.
    token = hashlib.sha1(
        f"{body.provider}|{body.voice_id}|{said}|{opts.speed}".encode()
    ).hexdigest()[:16]
    dest = preview_dir / f"voice_{token}.wav"
    normalize_to_wav(result.audio, dest, trim=True)
    return FileResponse(dest, media_type="audio/wav")


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/assets")
def list_assets(project_id: int, type: str | None = None,
                session: Session = Depends(get_session)) -> list[dict]:
    stmt = select(Asset).where(Asset.project_id == project_id).order_by(Asset.id.desc())
    if type:
        stmt = stmt.where(Asset.type == type)
    return [
        {
            "id": a.id, "type": a.type, "provider": a.source_provider,
            "license": a.license_str, "attribution": a.attribution_str,
            "width": a.width, "height": a.height, "duration": a.duration,
            "source_url": a.source_url, "url": f"/api/assets/{a.id}/file",
            "created_at": a.created_at,
        }
        for a in session.exec(stmt).all()
    ]


@router.get("/assets/{asset_id}/file")
def asset_file(asset_id: int, session: Session = Depends(get_session)) -> FileResponse:
    asset = session.get(Asset, asset_id)
    if not asset or not asset.local_path or not Path(asset.local_path).exists():
        raise HTTPException(404, "Asset file not found")
    return FileResponse(asset.local_path)


@router.post("/projects/{project_id}/assets/upload")
async def upload_asset(
    project_id: int,
    scene_id: int | None = Form(None),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    blob = await file.read()
    candidate = image_providers.AssetCandidate(
        url="", provider="upload", license="Uploaded by the user",
        attribution="Own material", title=file.filename or "upload",
        extra={"bytes": blob},
    )
    try:
        stored = image_service.store_candidate(project_id, candidate)
    except Exception as exc:
        raise HTTPException(400, f"Could not read that image: {exc}") from exc

    asset = Asset(
        project_id=project_id, type="image", source_provider="upload",
        local_path=str(stored.path), license_str=stored.license,
        attribution_str=stored.attribution, width=stored.width, height=stored.height,
        content_hash=stored.content_hash,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)

    if scene_id:
        scene = session.get(Scene, scene_id)
        if scene:
            scene.asset_id = int(asset.id)
            scene.visual_source = "upload"
            session.add(scene)
            session.commit()
    return {"asset_id": asset.id, "url": f"/api/assets/{asset.id}/file"}


class SearchIn(BaseModel):
    provider: str = "pexels"
    query: str
    count: int = 12


@router.post("/assets/search")
def search_assets(body: SearchIn) -> dict[str, Any]:
    provider = image_providers.get_provider(body.provider)
    usable, reason = provider.available()
    if not usable:
        raise HTTPException(400, reason)
    try:
        results = provider.search(body.query, count=body.count)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    return {
        "results": [
            {"url": c.url, "thumb": c.thumb or c.url, "license": c.license,
             "attribution": c.attribution, "provider": c.provider,
             "width": c.width, "height": c.height, "title": c.title}
            for c in results
        ]
    }


class GenerateIn(BaseModel):
    project_id: int
    scene_id: int
    provider: str = "placeholder"
    prompt: str = ""


@router.post("/assets/generate")
def generate_asset(body: GenerateIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    scene = session.get(Scene, body.scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    try:
        stored = image_service.source_image(
            project_id=body.project_id,
            prompt=body.prompt or scene.image_prompt,
            provider_name=body.provider,
            depicts_real_person=scene.depicts_real_person,
        )
    except image_service.RealPersonBlocked as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc

    asset = Asset(
        project_id=body.project_id, type="image", source_provider=stored.provider,
        source_url=stored.source_url, local_path=str(stored.path),
        license_str=stored.license, attribution_str=stored.attribution,
        width=stored.width, height=stored.height, content_hash=stored.content_hash,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    scene.asset_id = int(asset.id)
    session.add(scene)
    session.commit()
    return {"asset_id": asset.id, "url": f"/api/assets/{asset.id}/file"}


# ---------------------------------------------------------------------------
# Renders
# ---------------------------------------------------------------------------

@router.get("/renders/{render_id}/file")
def render_file(render_id: int, session: Session = Depends(get_session)) -> FileResponse:
    row = session.get(RenderOutput, render_id)
    if not row or not Path(row.local_path).exists():
        raise HTTPException(404, "Render not found")
    return FileResponse(row.local_path, media_type="video/mp4",
                        filename=Path(row.local_path).name)
