"""JobQueue interface and the context handed to job handlers.

Kept separate from the SQLite implementation so Celery/RQ can be dropped in
later without touching a single handler.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")


class JobCanceled(Exception):
    """Raised inside a handler when the user cancels. Not an error state."""


class JobPaused(Exception):
    """Raised inside a handler when the user pauses. The checkpoint survives."""


class JobContext:
    """What a handler is given. The checkpoint is the point of this class.

    An hour-long render is hundreds of units of work. Handlers record each
    finished unit here, so a crash, a pause, or a restart resumes instead of
    starting the hour again.
    """

    def __init__(
        self,
        job_id: int,
        project_id: int | None,
        params: dict[str, Any],
        checkpoint: dict[str, Any],
        *,
        on_progress: Callable[[int, float, str, float | None], None],
        on_checkpoint: Callable[[int, dict[str, Any]], None],
        should_stop: Callable[[int], str | None],
    ) -> None:
        self.job_id = job_id
        self.project_id = project_id
        self.params = params
        self.checkpoint = checkpoint
        self._on_progress = on_progress
        self._on_checkpoint = on_checkpoint
        self._should_stop = should_stop
        self._started = time.monotonic()
        self._last_emit = 0.0

    # -- control ----------------------------------------------------------
    def check_stop(self) -> None:
        state = self._should_stop(self.job_id)
        if state == "canceled":
            raise JobCanceled()
        if state == "paused":
            raise JobPaused()

    # -- progress ---------------------------------------------------------
    def progress(self, fraction: float, message: str = "", *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < 0.4:
            return
        self._last_emit = now
        eta = None
        if fraction > 0.02:
            elapsed = now - self._started
            eta = max(0.0, elapsed / fraction - elapsed)
        self._on_progress(self.job_id, max(0.0, min(1.0, fraction)) * 100, message, eta)

    def track(self, items: Iterable[T], message: str = "") -> Iterable[T]:
        """Iterate with automatic progress and cancellation checks."""
        items = list(items)
        total = len(items) or 1
        for i, item in enumerate(items):
            self.check_stop()
            self.progress(i / total, f"{message} {i + 1}/{total}" if message else "")
            yield item
        self.progress(1.0, message, force=True)

    # -- checkpoint -------------------------------------------------------
    def done_units(self, key: str) -> set[str]:
        return set(self.checkpoint.get(key, []))

    def mark_done(self, key: str, unit: str) -> None:
        bucket = self.checkpoint.setdefault(key, [])
        if unit not in bucket:
            bucket.append(unit)
        self._on_checkpoint(self.job_id, self.checkpoint)

    def set_state(self, key: str, value: Any) -> None:
        self.checkpoint[key] = value
        self._on_checkpoint(self.job_id, self.checkpoint)


Handler = Callable[[JobContext], dict[str, Any]]


class JobQueue(ABC):
    @abstractmethod
    def register(self, job_type: str, handler: Handler) -> None: ...

    @abstractmethod
    def submit(
        self, job_type: str, *, project_id: int | None = None,
        params: dict[str, Any] | None = None, parent_batch_id: int | None = None,
    ) -> int: ...

    @abstractmethod
    def cancel(self, job_id: int) -> bool: ...

    @abstractmethod
    def pause(self, job_id: int) -> bool: ...

    @abstractmethod
    def resume(self, job_id: int) -> bool: ...

    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...
