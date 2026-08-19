"""Reclaiming disk, and deleting a project properly.

A finished hour-long video leaves gigabytes behind: scene clips, downloaded
footage, per-scene audio, previews and the renders themselves. Deleting the
database row and leaving all of that on disk would be the worst of both worlds,
so removal here always means the files too.
"""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, select

from ..config import settings
from ..db import session_scope
from ..models import Asset, Chapter, Job, Project, RenderOutput, Scene, Script

log = logging.getLogger("casefile.housekeeping")

# Sub-directories of a project, and whether they are regenerable.
DISPOSABLE = ("clips", "previews", "cache")     # rebuilt from sources on demand
SOURCES = ("assets", "audio")                   # re-downloading these costs money or time
RENDERS = ("renders",)


@dataclass
class Usage:
    total: int = 0
    clips: int = 0
    renders: int = 0
    sources: int = 0
    previews: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "total_bytes": self.total, "clip_bytes": self.clips,
            "render_bytes": self.renders, "source_bytes": self.sources,
            "preview_bytes": self.previews,
        }


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def usage(project_id: int) -> Usage:
    base = settings.project_dir(project_id)
    if not base.exists():
        return Usage()
    clips = sum(_dir_size(d) for d in base.glob("clips*"))
    return Usage(
        total=_dir_size(base),
        clips=clips,
        renders=_dir_size(base / "renders"),
        sources=sum(_dir_size(base / name) for name in SOURCES),
        previews=_dir_size(base / "previews"),
    )


def _remove(path: Path) -> int:
    freed = _dir_size(path)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    return freed


def clear_workspace(session: Session, project_id: int, *, drop_renders: bool = False,
                    drop_sources: bool = False) -> dict[str, int]:
    """Free space without losing the project.

    The default clears only what can be rebuilt: scene clips, previews, caches.
    Sources and finished renders are kept unless asked for, because those cost
    money or an hour of encoding to recreate.
    """
    base = settings.project_dir(project_id)
    freed = 0
    for name in DISPOSABLE:
        freed += _remove(base / name)
    for extra in base.glob("clips_ch*"):
        freed += _remove(extra)

    if drop_renders:
        freed += _remove(base / "renders")
        for row in session.exec(
            select(RenderOutput).where(RenderOutput.project_id == project_id)
        ).all():
            session.delete(row)

    if drop_sources:
        for name in SOURCES:
            freed += _remove(base / name)

        # Scenes must let go of their assets *before* the assets are deleted,
        # or the foreign key blocks the whole operation.
        for scene in session.exec(select(Scene).where(Scene.project_id == project_id)).all():
            scene.asset_id = None
            scene.audio_asset_id = None
            scene.clip_path = ""
            scene.clip_hash = ""
            scene.status = "new"
            scene.duration = 0.0
            scene.start_time = 0.0
            scene.end_time = 0.0
            scene.words_json = []
            session.add(scene)
        session.flush()

        for asset in session.exec(select(Asset).where(Asset.project_id == project_id)).all():
            session.delete(asset)

    # Clip paths recorded on scenes are stale whatever was cleared.
    for scene in session.exec(select(Scene).where(Scene.project_id == project_id)).all():
        if scene.clip_path:
            scene.clip_path = ""
            scene.clip_hash = ""
            session.add(scene)

    session.commit()
    log.info("cleared %.1f MB from project %s", freed / 1e6, project_id)
    return {"freed_bytes": freed}


def delete_render(session: Session, render_id: int) -> dict[str, int]:
    """Remove one finished video and its sidecars."""
    row = session.get(RenderOutput, render_id)
    if row is None:
        raise ValueError("Render not found.")

    freed = 0
    for candidate in (row.local_path, row.chapters_txt_path):
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists() and path.is_file():
            freed += path.stat().st_size
            path.unlink(missing_ok=True)
    # The .srt sits beside the mp4 under the same stem.
    if row.local_path:
        srt = Path(row.local_path).with_suffix(".srt")
        if srt.exists():
            freed += srt.stat().st_size
            srt.unlink(missing_ok=True)

    session.delete(row)
    session.commit()
    return {"freed_bytes": freed}


def stop_jobs_for(project_id: int, *, timeout: float = 20.0) -> list[int]:
    """Cancel this project's jobs and wait for the workers to let go.

    Deleting the rows out from under a running handler does not stop it: it
    keeps going and fails scene by scene on foreign-key errors, writing to a
    project that no longer exists. Ask it to stop, then wait.
    """
    from ..main import job_queue      # the running singleton

    stopped: list[int] = []
    with session_scope() as session:
        live = session.exec(
            select(Job).where(Job.project_id == project_id,
                              Job.status.in_(("queued", "running", "paused")))
        ).all()
        ids = [int(j.id) for j in live]

    for job_id in ids:
        if job_queue.cancel(job_id):
            stopped.append(job_id)

    deadline = time.monotonic() + timeout
    while ids and time.monotonic() < deadline:
        with session_scope() as session:
            still = session.exec(
                select(Job).where(Job.id.in_(ids),
                                  Job.status.in_(("queued", "running")))
            ).all()
        if not still:
            break
        time.sleep(0.25)
    return stopped


def delete_project(session: Session, project_id: int) -> dict[str, int]:
    """Delete a project, everything it made, and everything it downloaded."""
    project = session.get(Project, project_id)
    if project is None:
        raise ValueError("Project not found.")

    # Anything still running has to be told to stop first, or it carries on
    # writing to a project that is being deleted underneath it.
    stopped = stop_jobs_for(project_id)
    if stopped:
        log.info("stopped %d job(s) before deleting project %s", len(stopped), project_id)
        session.expire_all()

    base = settings.project_dir(project_id)
    freed = _dir_size(base)

    # Scenes reference assets and chapters, so they go first.
    for model in (Scene, Chapter, Script, RenderOutput, Asset):
        for row in session.exec(select(model).where(model.project_id == project_id)).all():
            session.delete(row)
    for job in session.exec(select(Job).where(Job.project_id == project_id)).all():
        session.delete(job)
    session.delete(project)
    session.commit()

    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    log.info("deleted project %s and %.1f MB", project_id, freed / 1e6)
    return {"freed_bytes": freed}
