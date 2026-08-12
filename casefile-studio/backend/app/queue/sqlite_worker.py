"""SQLite-backed persistent job queue. No Redis, no external broker.

Jobs survive a restart: anything left `running` when the process died is
requeued on startup and picks up from its checkpoint.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select

from .. import events
from ..db import session_scope
from ..models import Job
from .base import Handler, JobCanceled, JobContext, JobPaused, JobQueue

log = logging.getLogger("casefile.queue")


class SqliteJobQueue(JobQueue):
    def __init__(self, workers: int = 2, poll_interval: float = 0.5) -> None:
        self._handlers: dict[str, Handler] = {}
        self._workers = workers
        self._poll = poll_interval
        self._threads: list[threading.Thread] = []
        self._stopping = threading.Event()
        # Control flags live in memory so a cancel takes effect mid-step
        # without a handler needing to hit the database on every loop.
        self._control: dict[int, str] = {}
        self._claim_lock = threading.Lock()

    # -- registration -----------------------------------------------------
    def register(self, job_type: str, handler: Handler) -> None:
        self._handlers[job_type] = handler

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        # Clearing the flag makes start/stop/start work within one process.
        # Without it a restarted queue spawns workers that exit immediately and
        # every job sits in 'queued' forever.
        self._stopping.clear()
        self._threads = [t for t in self._threads if t.is_alive()]
        if self._threads:
            log.debug("job queue already running")
            return

        self._requeue_orphans()
        for i in range(self._workers):
            t = threading.Thread(target=self._loop, name=f"casefile-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("job queue started with %d workers", self._workers)

    def stop(self) -> None:
        self._stopping.set()
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads = []

    def _requeue_orphans(self) -> None:
        with session_scope() as s:
            orphans = s.exec(select(Job).where(Job.status == "running")).all()
            for job in orphans:
                job.status = "queued"
                job.message = "resumed after restart"
                s.add(job)
            if orphans:
                log.info("requeued %d interrupted job(s)", len(orphans))

    # -- submission -------------------------------------------------------
    def submit(
        self, job_type: str, *, project_id: int | None = None,
        params: dict[str, Any] | None = None, parent_batch_id: int | None = None,
    ) -> int:
        if job_type not in self._handlers:
            raise KeyError(f"no handler registered for job type {job_type!r}")
        with session_scope() as s:
            job = Job(
                type=job_type, project_id=project_id,
                params_json=params or {}, parent_batch_id=parent_batch_id,
            )
            s.add(job)
            s.flush()
            job_id = job.id
        events.publish({"kind": "job.queued", "job_id": job_id, "type": job_type, "project_id": project_id})
        return int(job_id)

    # -- control ----------------------------------------------------------
    def _set_status(self, job_id: int, status: str, message: str = "") -> bool:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if not job:
                return False
            if job.status in ("done", "error", "canceled"):
                return False
            if job.status == "queued":
                job.status = status if status != "paused" else "paused"
                job.message = message
                s.add(job)
            else:
                self._control[job_id] = status
                job.message = message
                s.add(job)
        events.publish({"kind": "job.control", "job_id": job_id, "status": status})
        return True

    def cancel(self, job_id: int) -> bool:
        return self._set_status(job_id, "canceled", "canceling")

    def pause(self, job_id: int) -> bool:
        return self._set_status(job_id, "paused", "pausing")

    def resume(self, job_id: int) -> bool:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if not job or job.status not in ("paused", "error"):
                return False
            job.status = "queued"
            job.message = "resuming"
            job.error_str = ""
            s.add(job)
        self._control.pop(job_id, None)
        events.publish({"kind": "job.control", "job_id": job_id, "status": "queued"})
        return True

    # -- worker loop ------------------------------------------------------
    def _claim(self) -> Job | None:
        with self._claim_lock, session_scope() as s:
            job = s.exec(
                select(Job).where(Job.status == "queued").order_by(Job.id).limit(1)
            ).first()
            if not job:
                return None
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            job.message = "starting"
            s.add(job)
            s.flush()
            s.refresh(job)
            s.expunge(job)
            return job

    def _loop(self) -> None:
        while not self._stopping.is_set():
            job = self._claim()
            if job is None:
                time.sleep(self._poll)
                continue
            self._run_job(job)

    def _run_job(self, job: Job) -> None:
        job_id = int(job.id or 0)
        self._control.pop(job_id, None)
        handler = self._handlers.get(job.type)
        if handler is None:
            self._finish(job_id, "error", error=f"no handler for job type {job.type!r}")
            return

        ctx = JobContext(
            job_id=job_id,
            project_id=job.project_id,
            params=dict(job.params_json or {}),
            checkpoint=dict(job.checkpoint_json or {}),
            on_progress=self._emit_progress,
            on_checkpoint=self._save_checkpoint,
            should_stop=lambda jid: self._control.get(jid),
        )
        events.publish({"kind": "job.started", "job_id": job_id, "type": job.type, "project_id": job.project_id})

        try:
            result = handler(ctx) or {}
            self._finish(job_id, "done", result=result)
        except JobCanceled:
            self._finish(job_id, "canceled")
        except JobPaused:
            self._finish(job_id, "paused")
        except Exception as exc:  # handler bug or provider failure
            log.exception("job %s (%s) failed", job_id, job.type)
            self._finish(job_id, "error", error=f"{type(exc).__name__}: {exc}",
                         detail=traceback.format_exc(limit=6))
        finally:
            self._control.pop(job_id, None)

    # -- persistence ------------------------------------------------------
    def _emit_progress(self, job_id: int, percent: float, message: str, eta: float | None) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job:
                job.progress = percent
                job.eta_sec = eta
                if message:
                    job.message = message
                s.add(job)
        events.publish({
            "kind": "job.progress", "job_id": job_id,
            "progress": round(percent, 1), "message": message, "eta_sec": eta,
        })

    def _save_checkpoint(self, job_id: int, checkpoint: dict[str, Any]) -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if job:
                job.checkpoint_json = dict(checkpoint)
                s.add(job)

    def _finish(self, job_id: int, status: str, *, result: dict | None = None,
                error: str = "", detail: str = "") -> None:
        with session_scope() as s:
            job = s.get(Job, job_id)
            if not job:
                return
            job.status = status
            job.finished_at = datetime.now(timezone.utc)
            job.error_str = (error + ("\n" + detail if detail else "")).strip()
            if result is not None:
                job.result_json = result
            if status == "done":
                job.progress = 100.0
                job.message = "finished"
            elif status == "paused":
                job.message = "paused - resume to continue"
            elif status == "canceled":
                job.message = "canceled"
            else:
                job.message = error[:200] or "failed"
            s.add(job)
        events.publish({
            "kind": "job.finished", "job_id": job_id, "status": status,
            "error": error, "result": result or {},
        })
