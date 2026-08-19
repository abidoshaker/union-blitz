"""Jobs, batch operations, render, and the progress WebSocket."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from sqlmodel import Session, select

from .. import events
from ..db import get_session
from ..models import Chapter, Job, Project, RenderOutput, Scene
from ..services import render_service
from ..services.pipeline import _render_opts, project_settings
from ..services.tts_service import estimate_batch_cost

router = APIRouter(prefix="/api", tags=["jobs"])


def queue():
    from ..main import job_queue

    return job_queue


class RunIn(BaseModel):
    scene_ids: list[int] | None = None
    chapter_id: int | None = None
    variant: str = "16:9"
    overrides: dict[str, Any] = {}


def _shape(job: Job) -> dict[str, Any]:
    return {
        "id": job.id, "type": job.type, "status": job.status,
        "progress": round(job.progress, 1), "message": job.message,
        "project_id": job.project_id, "eta_sec": job.eta_sec,
        "error": job.error_str, "result": job.result_json,
        "created_at": job.created_at, "finished_at": job.finished_at,
        "parent_batch_id": job.parent_batch_id,
    }


@router.get("/jobs")
def list_jobs(
    project_id: int | None = None,
    active_only: bool = False,
    limit: int = Query(40, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[dict]:
    stmt = select(Job).order_by(Job.id.desc()).limit(limit)
    if project_id is not None:
        stmt = stmt.where(Job.project_id == project_id)
    if active_only:
        stmt = stmt.where(Job.status.in_(["queued", "running", "paused"]))
    return [_shape(j) for j in session.exec(stmt).all()]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, session: Session = Depends(get_session)) -> dict:
    job = session.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _shape(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int) -> dict:
    return {"ok": queue().cancel(job_id)}


@router.post("/jobs/{job_id}/pause")
def pause_job(job_id: int) -> dict:
    return {"ok": queue().pause(job_id)}


@router.post("/jobs/{job_id}/resume")
def resume_job(job_id: int) -> dict:
    return {"ok": queue().resume(job_id)}


# ---------------------------------------------------------------------------
# Running stages
# ---------------------------------------------------------------------------

@router.post("/projects/{project_id}/run/{stage}", status_code=202)
def run_stage(project_id: int, stage: str, body: RunIn,
              session: Session = Depends(get_session)) -> dict:
    """stage in {segment, narrate, images, render, build}."""
    if stage not in ("segment", "narrate", "images", "render", "build"):
        raise HTTPException(400, f"Unknown stage {stage!r}")
    if not session.get(Project, project_id):
        raise HTTPException(404, "Project not found")

    params: dict[str, Any] = {"project_id": project_id, **body.overrides}
    if body.scene_ids:
        params["scene_ids"] = body.scene_ids
    if body.chapter_id is not None:
        params["chapter_id"] = body.chapter_id
    if stage == "render":
        params["variant"] = body.variant

    job_id = queue().submit(stage, project_id=project_id, params=params)
    return {"job_id": job_id, "stage": stage}


@router.post("/projects/{project_id}/render/preview", status_code=202)
def render_preview(project_id: int, chapter_id: int | None = None,
                   session: Session = Depends(get_session)) -> dict:
    """Render one chapter.

    The most important endpoint in the app for hour-long work: nobody should
    find out the voice is wrong after a three-hour render.
    """
    if chapter_id is None:
        chapter = session.exec(
            select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order_index)
        ).first()
        if not chapter:
            raise HTTPException(400, "This project has no chapters yet - segment the script first.")
        chapter_id = int(chapter.id)

    job_id = queue().submit(
        "render", project_id=project_id,
        params={"project_id": project_id, "chapter_id": chapter_id, "variant": "16:9"},
    )
    return {"job_id": job_id, "chapter_id": chapter_id}


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------

class BatchIn(BaseModel):
    project_id: int
    scene_ids: list[int] = []
    chapter_id: int | None = None
    op: str
    payload: dict[str, Any] = {}


@router.post("/batch/preview")
def batch_preview(body: BatchIn, session: Session = Depends(get_session)) -> dict:
    """What a batch would do, and what it would cost, before it runs."""
    scenes = _batch_scenes(session, body)
    project = session.get(Project, body.project_id)
    cfg = project_settings(project) if project else {}
    cfg.update(body.payload)

    changes: list[dict] = []
    cost = {"cost_usd": 0.0}
    if body.op in ("regenerate-audio", "re-render"):
        cost = estimate_batch_cost([s.text for s in scenes], str(cfg.get("tts_provider", "draft")))
    if body.op == "find-replace":
        find = str(body.payload.get("find", ""))
        replace = str(body.payload.get("replace", ""))
        for scene in scenes:
            if find and find in scene.text:
                changes.append({
                    "scene_id": scene.id, "order_index": scene.order_index,
                    "before": scene.text, "after": scene.text.replace(find, replace),
                })
    elif body.op == "swap-images":
        changes = [
            {"scene_id": s.id, "order_index": s.order_index,
             "before": s.visual_source, "after": body.payload.get("visual_source", s.visual_source)}
            for s in scenes
        ]
    elif body.op == "source-images":
        # Only the scenes with nothing yet, so a batch cannot quietly discard
        # a picture that was chosen by hand.
        changes = [
            {"scene_id": s.id, "order_index": s.order_index, "before": "", "after": "sourced"}
            for s in scenes if not s.asset_id
        ]
    else:
        changes = [{"scene_id": s.id, "order_index": s.order_index} for s in scenes]

    counted = body.op in ("find-replace", "source-images")
    return {
        "op": body.op,
        "scene_count": len(scenes),
        "affected": len(changes) if counted else len(scenes),
        "estimated_cost_usd": round(float(cost.get("cost_usd", 0.0)), 4),
        "changes": changes[:50],
        "truncated": len(changes) > 50,
    }


@router.post("/batch/apply", status_code=202)
def batch_apply(body: BatchIn, session: Session = Depends(get_session)) -> dict:
    scenes = _batch_scenes(session, body)
    if not scenes:
        raise HTTPException(400, "No scenes selected.")

    if body.op == "find-replace":
        find = str(body.payload.get("find", ""))
        replace = str(body.payload.get("replace", ""))
        if not find:
            raise HTTPException(400, "Nothing to find.")
        touched = 0
        for scene in scenes:
            if find in scene.text:
                scene.text = scene.text.replace(find, replace)
                scene.audio_asset_id = None
                scene.status = "new"
                session.add(scene)
                touched += 1
        session.commit()
        return {"applied": touched}

    if body.op == "apply-settings":
        allowed = {"kenburns", "visual_source", "ai_disclaimer", "depicts_real_person"}
        updates = {k: v for k, v in body.payload.items() if k in allowed}
        for scene in scenes:
            for key, value in updates.items():
                setattr(scene, key, value)
            session.add(scene)
        session.commit()
        return {"applied": len(scenes)}

    if body.op in ("set-visual-source", "swap-images", "source-images"):
        # swap-images throws away what is there and finds something else.
        # source-images fills in the scenes that have nothing, leaving pictures
        # you have already chosen or fixed by hand alone.
        source = body.payload.get("visual_source")
        replacing = body.op != "source-images"
        targets = scenes if replacing else [s for s in scenes if not s.asset_id]
        if not targets:
            raise HTTPException(400, "Every selected scene already has a picture.")

        for scene in targets:
            if source:
                scene.visual_source = source
            if replacing:
                scene.asset_id = None
            session.add(scene)
        session.commit()

        job_id = queue().submit("images", project_id=body.project_id, params={
            "project_id": body.project_id,
            "scene_ids": [int(s.id) for s in targets],
            # `image_sources` and `video_sources` are lists of libraries to try,
            # and both are project settings, so they pass straight through as
            # a per-run override.
            **{k: v for k, v in body.payload.items() if k != "visual_source"},
        })
        return {"applied": len(targets), "job_id": job_id}

    if body.op == "regenerate-audio":
        for scene in scenes:
            scene.audio_asset_id = None
            scene.status = "new"
            session.add(scene)
        session.commit()
        job_id = queue().submit("narrate", project_id=body.project_id, params={
            "project_id": body.project_id,
            "scene_ids": [int(s.id) for s in scenes],
            **body.payload,
        })
        return {"applied": len(scenes), "job_id": job_id}

    if body.op == "re-render":
        job_id = queue().submit("render", project_id=body.project_id,
                                params={"project_id": body.project_id, **body.payload})
        return {"applied": len(scenes), "job_id": job_id}

    raise HTTPException(400, f"Unknown batch op {body.op!r}")


def _batch_scenes(session: Session, body: BatchIn) -> list[Scene]:
    stmt = select(Scene).where(Scene.project_id == body.project_id)
    if body.scene_ids:
        stmt = stmt.where(Scene.id.in_(body.scene_ids))
    elif body.chapter_id is not None:
        stmt = stmt.where(Scene.chapter_id == body.chapter_id)
    return list(session.exec(stmt.order_by(Scene.order_index)).all())


# ---------------------------------------------------------------------------
# Renders and preflight
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/renders")
def list_renders(project_id: int, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.exec(
        select(RenderOutput).where(RenderOutput.project_id == project_id).order_by(RenderOutput.id.desc())
    ).all()
    return [
        {
            "id": r.id, "variant": r.variant, "path": r.local_path,
            "duration": r.duration, "size_bytes": r.size_bytes,
            "created_at": r.created_at, "chapters_txt": r.chapters_txt_path,
            "ad_breaks": r.ad_breaks_json,
            "download_url": f"/api/renders/{r.id}/file",
        }
        for r in rows
    ]


@router.delete("/renders/{render_id}")
def delete_render(render_id: int, session: Session = Depends(get_session)) -> dict:
    """Remove one finished video, its chapters file and its subtitles."""
    from ..services import housekeeping

    try:
        return housekeeping.delete_render(session, render_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/projects/{project_id}/preflight")
def preflight(project_id: int, session: Session = Depends(get_session)) -> dict:
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    scenes = session.exec(select(Scene).where(Scene.project_id == project_id)).all()
    cfg = project_settings(project)
    opts = _render_opts(cfg)

    total = sum(s.duration for s in scenes)
    if not total:
        from ..services.segmentation import estimate_runtime

        total = sum(estimate_runtime(s.text) for s in scenes)

    from ..config import settings as cfg_settings

    report = render_service.preflight(
        len(scenes), total, opts, cfg_settings.project_dir(project_id) / "clips"
    )
    report.update({
        "scene_count": len(scenes),
        "estimated_video_sec": round(total, 1),
        "narration_cost_usd": estimate_batch_cost(
            [s.text for s in scenes], str(cfg.get("tts_provider", "draft"))
        )["cost_usd"],
    })
    return report


# ---------------------------------------------------------------------------
# Progress socket
# ---------------------------------------------------------------------------

@router.websocket("/ws/jobs")
async def ws_jobs(websocket: WebSocket) -> None:
    await websocket.accept()
    inbox = events.subscribe()
    try:
        await websocket.send_json({"kind": "hello"})
        while True:
            try:
                event = await asyncio.wait_for(inbox.get(), timeout=25.0)
            except asyncio.TimeoutError:
                await websocket.send_json({"kind": "ping"})
                continue
            await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        events.unsubscribe(inbox)
