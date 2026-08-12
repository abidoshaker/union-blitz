"""Scenes, paginated. A 240-scene project must never come back in one payload."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, func, select

from ..db import get_session
from ..models import Asset, Scene

router = APIRouter(prefix="/api", tags=["scenes"])


class ScenePatch(BaseModel):
    text: str | None = None
    image_prompt: str | None = None
    visual_source: str | None = None
    kenburns: str | None = None
    depicts_real_person: bool | None = None
    ai_disclaimer: bool | None = None
    notes: str | None = None


class ReorderIn(BaseModel):
    scene_ids: list[int]


def _shape(scene: Scene, assets: dict[int, Asset]) -> dict[str, Any]:
    image = assets.get(scene.asset_id or -1)
    audio = assets.get(scene.audio_asset_id or -1)
    return {
        "id": scene.id,
        "order_index": scene.order_index,
        "chapter_id": scene.chapter_id,
        "text": scene.text,
        "image_prompt": scene.image_prompt,
        "visual_source": scene.visual_source,
        "kenburns": scene.kenburns,
        "status": scene.status,
        "duration": scene.duration,
        "start_time": scene.start_time,
        "end_time": scene.end_time,
        "depicts_real_person": scene.depicts_real_person,
        "ai_disclaimer": scene.ai_disclaimer,
        "notes": scene.notes,
        "has_audio": bool(scene.audio_asset_id),
        "has_image": bool(scene.asset_id),
        "image_url": f"/api/assets/{image.id}/file" if image else "",
        "audio_url": f"/api/assets/{audio.id}/file" if audio else "",
        "license": image.license_str if image else "",
        "attribution": image.attribution_str if image else "",
    }


@router.get("/projects/{project_id}/scenes")
def list_scenes(
    project_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(60, ge=1, le=200),
    chapter_id: int | None = None,
    filter: str = Query("", description="needs_image|needs_audio|real_person|ready|error"),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(Scene).where(Scene.project_id == project_id)
    if chapter_id is not None:
        stmt = stmt.where(Scene.chapter_id == chapter_id)
    if filter == "needs_image":
        stmt = stmt.where(Scene.asset_id.is_(None))
    elif filter == "needs_audio":
        stmt = stmt.where(Scene.audio_asset_id.is_(None))
    elif filter == "real_person":
        stmt = stmt.where(Scene.depicts_real_person == True)  # noqa: E712
    elif filter in ("ready", "error", "new"):
        stmt = stmt.where(Scene.status == filter)

    total = session.exec(
        select(func.count()).select_from(stmt.subquery())
    ).one()
    rows = session.exec(stmt.order_by(Scene.order_index).offset(offset).limit(limit)).all()

    asset_ids = {s.asset_id for s in rows} | {s.audio_asset_id for s in rows}
    assets = {
        int(a.id): a
        for a in session.exec(select(Asset).where(Asset.id.in_([i for i in asset_ids if i]))).all()
    } if any(asset_ids) else {}

    return {
        "total": int(total),
        "offset": offset,
        "limit": limit,
        "items": [_shape(s, assets) for s in rows],
    }


@router.patch("/scenes/{scene_id}")
def patch_scene(scene_id: int, body: ScenePatch, session: Session = Depends(get_session)) -> dict:
    scene = session.get(Scene, scene_id)
    if not scene:
        raise HTTPException(404, "Scene not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(scene, field, value)
    if body.text is not None:
        # The narration changed, so the cached audio no longer matches it.
        scene.audio_asset_id = None
        scene.status = "new"
    session.add(scene)
    session.commit()
    session.refresh(scene)
    return _shape(scene, {})


@router.post("/projects/{project_id}/scenes/reorder")
def reorder(project_id: int, body: ReorderIn, session: Session = Depends(get_session)) -> dict:
    scenes = {int(s.id): s for s in session.exec(
        select(Scene).where(Scene.project_id == project_id)).all()}
    for index, scene_id in enumerate(body.scene_ids):
        scene = scenes.get(scene_id)
        if scene:
            scene.order_index = index
            session.add(scene)
    session.commit()
    return {"reordered": len(body.scene_ids)}


@router.get("/projects/{project_id}/scenes/stats")
def stats(project_id: int, session: Session = Depends(get_session)) -> dict:
    scenes = session.exec(select(Scene).where(Scene.project_id == project_id)).all()
    return {
        "total": len(scenes),
        "needs_image": sum(1 for s in scenes if not s.asset_id),
        "needs_audio": sum(1 for s in scenes if not s.audio_asset_id),
        "real_person": sum(1 for s in scenes if s.depicts_real_person),
        "ready": sum(1 for s in scenes if s.status == "ready"),
        "narration_sec": round(sum(s.duration for s in scenes), 1),
    }
