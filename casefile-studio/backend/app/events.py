"""Progress fan-out from worker threads to WebSocket clients."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

_loop: asyncio.AbstractEventLoop | None = None
_subscribers: set[asyncio.Queue] = set()
_lock = threading.Lock()


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    with _lock:
        _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _lock:
        _subscribers.discard(q)


def publish(event: dict[str, Any]) -> None:
    """Safe to call from any thread."""
    if _loop is None:
        return
    with _lock:
        targets = list(_subscribers)
    if not targets:
        return

    def _deliver() -> None:
        for q in targets:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a slow client must not stall a render

    try:
        _loop.call_soon_threadsafe(_deliver)
    except RuntimeError:
        pass  # loop is shutting down
