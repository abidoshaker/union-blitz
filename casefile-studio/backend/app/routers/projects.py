"""Projects, scripts, chapters."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..models import Chapter, Project, RenderOutput, Scene, Script
from ..services import housekeeping, segmentation
from ..services.pipeline import project_settings

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    title: str
    genre_preset: str = "true-crime"
    settings: dict[str, Any] = {}


class ProjectPatch(BaseModel):
    title: str | None = None
    genre_preset: str | None = None
    status: str | None = None
    settings: dict[str, Any] | None = None


class ScriptIn(BaseModel):
    raw_text: str


def _summary(session: Session, project: Project) -> dict[str, Any]:
    scenes = session.exec(select(Scene).where(Scene.project_id == project.id)).all()
    script = session.exec(
        select(Script).where(Script.project_id == project.id).order_by(Script.id.desc())
    ).first()
    renders = session.exec(
        select(RenderOutput).where(RenderOutput.project_id == project.id).order_by(RenderOutput.id.desc())
    ).all()
    ready = sum(1 for s in scenes if s.status == "ready")
    return {
        "id": project.id,
        "title": project.title,
        "genre_preset": project.genre_preset,
        "status": project.status,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "settings": project_settings(project),
        "scene_count": len(scenes),
        "scenes_ready": ready,
        "scenes_with_audio": sum(1 for s in scenes if s.audio_asset_id),
        "scenes_with_image": sum(1 for s in scenes if s.asset_id),
        "flagged_real_person": sum(1 for s in scenes if s.depicts_real_person),
        "word_count": script.word_count if script else 0,
        "estimated_runtime_sec": script.estimated_runtime_sec if script else 0.0,
        "narration_sec": round(sum(s.duration for s in scenes), 1),
        "latest_render": renders[0].local_path if renders else "",
        "render_count": len(renders),
    }


@router.get("")
def list_projects(session: Session = Depends(get_session)) -> list[dict]:
    projects = session.exec(select(Project).order_by(Project.updated_at.desc())).all()
    return [_summary(session, p) for p in projects]


@router.post("", status_code=201)
def create_project(body: ProjectCreate, session: Session = Depends(get_session)) -> dict:
    project = Project(title=body.title.strip() or "Untitled case",
                      genre_preset=body.genre_preset, settings_json=body.settings)
    session.add(project)
    session.commit()
    session.refresh(project)
    settings.ensure_project_dirs(int(project.id))
    return _summary(session, project)


@router.get("/{project_id}")
def get_project(project_id: int, session: Session = Depends(get_session)) -> dict:
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return _summary(session, project)


@router.patch("/{project_id}")
def patch_project(project_id: int, body: ProjectPatch, session: Session = Depends(get_session)) -> dict:
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    from ..models import utcnow

    if body.title is not None:
        project.title = body.title
    if body.genre_preset is not None:
        project.genre_preset = body.genre_preset
    if body.status is not None:
        project.status = body.status
    if body.settings is not None:
        project.settings_json = {**(project.settings_json or {}), **body.settings}
    project.updated_at = utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)
    return _summary(session, project)


class ClearIn(BaseModel):
    drop_renders: bool = False
    drop_sources: bool = False


@router.delete("/{project_id}")
def delete_project(project_id: int, session: Session = Depends(get_session)) -> dict:
    """Delete the project and everything on disk that belongs to it.

    Removing the rows and leaving gigabytes of clips and downloads behind would
    be the worst of both worlds, so this always takes the files too.
    """
    try:
        return housekeeping.delete_project(session, project_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{project_id}/usage")
def project_usage(project_id: int) -> dict:
    """What this project is costing in disk, broken down by what it is."""
    return housekeeping.usage(project_id).as_dict()


@router.post("/{project_id}/clear")
def clear_project(project_id: int, body: ClearIn,
                  session: Session = Depends(get_session)) -> dict:
    """Free space without losing the project.

    By default this removes only what can be rebuilt - scene clips, previews,
    caches. Finished renders and downloaded sources are kept unless asked for,
    because those cost an hour of encoding or real money to recreate.
    """
    if not session.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    return housekeeping.clear_workspace(
        session, project_id,
        drop_renders=body.drop_renders, drop_sources=body.drop_sources,
    )


@router.get("/{project_id}/script")
def get_script(project_id: int, session: Session = Depends(get_session)) -> dict:
    script = session.exec(
        select(Script).where(Script.project_id == project_id).order_by(Script.id.desc())
    ).first()
    if not script:
        return {"raw_text": "", "word_count": 0, "estimated_runtime_sec": 0.0}
    return {
        "raw_text": script.raw_text,
        "word_count": script.word_count,
        "estimated_runtime_sec": script.estimated_runtime_sec,
    }


@router.post("/{project_id}/script")
def put_script(project_id: int, body: ScriptIn, session: Session = Depends(get_session)) -> dict:
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    raw = body.raw_text
    words = segmentation.word_count(raw)
    runtime = segmentation.estimate_runtime(raw)
    script = Script(project_id=project_id, raw_text=raw, word_count=words,
                    estimated_runtime_sec=runtime)
    session.add(script)
    project.status = "script"
    session.add(project)
    session.commit()
    return {
        "word_count": words,
        "estimated_runtime_sec": round(runtime, 1),
        "estimated_scenes": max(1, int(runtime / settings.scene_target_sec)),
    }


@router.get("/{project_id}/chapters")
def list_chapters(project_id: int, session: Session = Depends(get_session)) -> list[dict]:
    chapters = session.exec(
        select(Chapter).where(Chapter.project_id == project_id).order_by(Chapter.order_index)
    ).all()
    scenes = session.exec(select(Scene).where(Scene.project_id == project_id)).all()
    counts: dict[int, int] = {}
    for scene in scenes:
        if scene.chapter_id:
            counts[scene.chapter_id] = counts.get(scene.chapter_id, 0) + 1
    return [
        {
            "id": c.id, "title": c.title, "order_index": c.order_index,
            "start_time": c.start_time, "end_time": c.end_time,
            "scene_count": counts.get(int(c.id), 0),
        }
        for c in chapters
    ]


class ChapterPatch(BaseModel):
    title: str


@router.patch("/{project_id}/chapters/{chapter_id}")
def patch_chapter(project_id: int, chapter_id: int, body: ChapterPatch,
                  session: Session = Depends(get_session)) -> dict:
    chapter = session.get(Chapter, chapter_id)
    if not chapter or chapter.project_id != project_id:
        raise HTTPException(404, "Chapter not found")
    chapter.title = body.title
    session.add(chapter)
    session.commit()
    return {"id": chapter.id, "title": chapter.title}
