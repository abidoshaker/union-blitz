"""Runtime configuration and filesystem layout.

Deliberately dependency-free: this module is imported by tools that run before
the full environment is installed, and by doctor-style checks.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


def _load_env_local() -> None:
    """Read .env.local written by install-deps.bat (KEY=VALUE, # comments)."""
    for candidate in (ROOT_DIR / ".env.local", ROOT_DIR / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env_local()


def _find_binary(name: str) -> str | None:
    """Locate ffmpeg/ffprobe, preferring what the installer recorded."""
    hint = os.environ.get("CASEFILE_FFMPEG_BIN")
    if hint:
        for suffix in ("", ".exe"):
            candidate = Path(hint) / f"{name}{suffix}"
            if candidate.exists():
                return str(candidate)
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "CaseFileStudio" / "tools" / "ffmpeg" / "bin" / f"{name}.exe"
    if local.exists():
        return str(local)
    return shutil.which(name)


@dataclass
class Settings:
    host: str = os.environ.get("CASEFILE_HOST", "127.0.0.1")
    port: int = int(os.environ.get("CASEFILE_PORT", "8760"))

    data_dir: Path = field(default_factory=lambda: _env_path("CASEFILE_DATA_DIR", ROOT_DIR / "data"))
    models_dir: Path = field(default_factory=lambda: _env_path("CASEFILE_MODELS_DIR", ROOT_DIR / "models"))

    ffmpeg: str | None = field(default_factory=lambda: _find_binary("ffmpeg"))
    ffprobe: str | None = field(default_factory=lambda: _find_binary("ffprobe"))

    # Render defaults. Every one of these is overridable per project.
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 20
    preset: str = "veryfast"
    encoder: str = os.environ.get("CASEFILE_ENCODER", "libx264")
    kenburns_upscale: float = 2.0          # spec 15: modest, never 8000px
    transition_sec: float = 0.6
    loudness_lufs: float = -14.0

    # Long-form guardrails
    words_per_minute: int = 150
    max_scenes: int = 400
    scene_target_sec: float = 15.0
    render_workers: int = max(1, (os.cpu_count() or 2) - 1)   # leave a core for the user
    min_free_disk_gb: float = 12.0

    @property
    def db_path(self) -> Path:
        return self.data_dir / "casefile.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    def project_dir(self, project_id: int) -> Path:
        return self.data_dir / "projects" / str(project_id)

    def ensure_project_dirs(self, project_id: int) -> Path:
        base = self.project_dir(project_id)
        for sub in ("assets", "audio", "scenes", "clips", "renders", "cache"):
            (base / sub).mkdir(parents=True, exist_ok=True)
        return base

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
