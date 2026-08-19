"""CaseFile Studio backend entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import events, ffmpeg
from .config import ROOT_DIR, settings
from .db import init_db
from .queue import SqliteJobQueue
from .routers import jobs as jobs_router
from .routers import media as media_router
from .routers import projects as projects_router
from .routers import scenes as scenes_router
from .routers import settings as settings_router
from .services.pipeline import HANDLERS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("casefile")

# Scene clips render in their own pool inside the render job, so a small number
# of job workers is right: two long jobs at once, not twelve.
job_queue = SqliteJobQueue(workers=2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    init_db()
    events.bind_loop(asyncio.get_running_loop())

    for name, handler in HANDLERS.items():
        job_queue.register(name, handler)
    job_queue.start()

    caps = ffmpeg.capabilities()
    for problem in caps.problems():
        log.warning("STARTUP: %s", problem)
    log.info("CaseFile Studio ready on http://%s:%s", settings.host, settings.port)
    try:
        yield
    finally:
        job_queue.stop()


app = FastAPI(title="CaseFile Studio", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects_router.router)
app.include_router(scenes_router.router)
app.include_router(jobs_router.router)
app.include_router(settings_router.router)
app.include_router(media_router.router)


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception) -> JSONResponse:
    """Plain-language errors. The user is a beginner, not a stack trace reader."""
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": type(exc).__name__,
            "detail": str(exc),
            "hint": "Check the terminal window for the full message. "
                    "If this mentions ffmpeg, run doctor.bat.",
        },
    )


@app.get("/api/version")
def version() -> dict:
    caps = ffmpeg.capabilities()
    return {
        "name": "CaseFile Studio",
        "version": "0.1.0",
        "ffmpeg_ok": caps.ok,
        "workers": settings.render_workers,
    }


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="static-assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    def no_frontend() -> JSONResponse:
        return JSONResponse({
            "message": "Backend is running, but the frontend has not been built.",
            "fix": "Run: cd frontend && npm install && npm run build",
            "api_docs": "/docs",
        })
