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
    # Video and clip-audio controls
    media_kind: str | None = None
    media_in: float | None = None
    audio_mode: str | None = None     # narration | soundbite | ambient
    blur_faces: bool | None = None


class ReorderIn(BaseModel):
    scene_ids: list[int]


class MoveIn(BaseModel):
    scene_ids: list[int]
    to_index: int


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
        "media_kind": scene.media_kind,
        "media_in": scene.media_in,
        "audio_mode": scene.audio_mode,
        "blur_faces": scene.blur_faces,
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
    if body.audio_mode is not None:
        # A soundbite takes its audio from the clip and a narration scene takes
        # it from TTS, so switching between them invalidates whatever is cached.
        scene.audio_asset_id = None
        scene.status = "new"
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
    """Rewrite the whole running order.

    The full set of scene ids is required. The storyboard is paginated, so
    accepting a partial list would silently renumber one page over the top of
    the rest of the project.
    """
    scenes = _ordered(session, project_id)
    known = {int(s.id) for s in scenes}
    given = list(dict.fromkeys(body.scene_ids))

    if set(given) != known:
        missing, extra = known - set(given), set(given) - known
        raise HTTPException(
            400,
            "Reorder needs every scene in the project. "
            f"Missing {len(missing)}, unknown {len(extra)}. "
            "To move a few scenes, use /scenes/move instead.",
        )

    return _apply_order(session, scenes, given)


@router.post("/projects/{project_id}/scenes/move")
def move(project_id: int, body: MoveIn, session: Session = Depends(get_session)) -> dict:
    """Move one or more scenes to a new position, keeping their relative order.

    This is what the storyboard uses: the client only ever holds a page of an
    hour-long project, so it names the scenes that moved and where they land,
    and the server does the reindexing against the complete list.
    """
    scenes = _ordered(session, project_id)
    order = [int(s.id) for s in scenes]
    moving = [i for i in dict.fromkeys(body.scene_ids) if i in set(order)]
    if not moving:
        raise HTTPException(400, "None of those scenes belong to this project.")

    target = max(0, min(int(body.to_index), len(order) - len(moving)))
    remainder = [i for i in order if i not in set(moving)]
    moving.sort(key=order.index)          # preserve their existing relative order
    new_order = remainder[:target] + moving + remainder[target:]

    return _apply_order(session, scenes, new_order)


def _ordered(session: Session, project_id: int) -> list[Scene]:
    return list(session.exec(
        select(Scene).where(Scene.project_id == project_id).order_by(Scene.order_index)
    ).all())


def _apply_order(session: Session, scenes: list[Scene], order: list[int]) -> dict[str, Any]:
    """Write the new indices, fix chapter membership, and drop stale timings."""
    by_id = {int(s.id): s for s in scenes}
    moved = 0

    for index, scene_id in enumerate(order):
        scene = by_id[scene_id]
        if scene.order_index != index:
            scene.order_index = index
            moved += 1
        # Positions changed, so every cached start/end time is now a lie. They
        # are recomputed from the narration on the next render.
        scene.start_time = 0.0
        scene.end_time = 0.0
        session.add(scene)

    chapters_changed = _reassign_chapters([by_id[i] for i in order])
    session.commit()
    return {"reordered": len(order), "moved": moved, "chapters_changed": chapters_changed}


def _reassign_chapters(ordered_scenes: list[Scene]) -> int:
    """Keep chapters contiguous by having each scene adopt its predecessor's.

    A scene dragged from chapter 4 into chapter 1 belongs to chapter 1 now.
    Without this, chapter membership interleaves and the YouTube chapter
    timestamps come out in the wrong order.
    """
    changed = 0
    current: int | None = None
    for i, scene in enumerate(ordered_scenes):
        if i == 0:
            current = scene.chapter_id
            continue
        if scene.chapter_id != current:
            # A scene that still leads a run of its own chapter starts it here;
            # otherwise it joins the chapter it now sits inside.
            following = ordered_scenes[i + 1].chapter_id if i + 1 < len(ordered_scenes) else None
            if scene.chapter_id is not None and scene.chapter_id == following:
                current = scene.chapter_id
            else:
                scene.chapter_id = current
                changed += 1
    return changed


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
