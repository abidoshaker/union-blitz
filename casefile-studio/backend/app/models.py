"""SQLite tables. Mirrors section 3 of BUILD_SPEC.md."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import Column, Index
from sqlalchemy.types import JSON, Text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    genre_preset: str = "true-crime"
    status: str = "draft"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    settings_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class Script(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    raw_text: str = Field(sa_column=Column(Text))
    word_count: int = 0
    estimated_runtime_sec: float = 0.0
    segmentation_checksum: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Chapter(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    order_index: int = 0
    title: str = ""
    start_time: float = 0.0
    end_time: float = 0.0


class Scene(SQLModel, table=True):
    """One narration beat: a slice of the script, one still, one audio file."""

    __table_args__ = (Index("ix_scene_project_order", "project_id", "order_index"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    chapter_id: Optional[int] = Field(default=None, foreign_key="chapter.id")
    order_index: int = 0

    text: str = Field(sa_column=Column(Text))
    # Offsets back into Script.raw_text. These are what prove the narration is
    # the author's words and not an LLM paraphrase (spec 15.2).
    source_char_start: int = 0
    source_char_end: int = 0

    start_time: float = 0.0
    end_time: float = 0.0
    duration: float = 0.0

    image_prompt: str = ""
    visual_source: str = "stock"          # 'ai' | 'stock' | 'upload'
    asset_id: Optional[int] = Field(default=None, foreign_key="asset.id")
    audio_asset_id: Optional[int] = Field(default=None, foreign_key="asset.id")

    kenburns: str = "auto"
    status: str = "new"                   # new | audio_ready | visual_ready | ready | error
    content_hash: str = ""
    clip_path: str = ""
    clip_hash: str = ""

    ai_disclaimer: bool = False
    depicts_real_person: bool = False
    notes: str = ""
    words_json: list[Any] = Field(default_factory=list, sa_column=Column(JSON))


class Asset(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    type: str                              # image | audio | video | music
    source_provider: str = ""
    source_url: str = ""
    local_path: str = ""
    license_str: str = ""
    attribution_str: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    content_hash: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Voice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str
    external_voice_id: str = ""
    title: str = ""
    is_clone: bool = False
    reference_audio_path: str = ""
    sample_path: str = ""
    tags_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    license_note: str = ""
    commercial_ok: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id", index=True)
    type: str
    status: str = "queued"                 # queued|running|paused|done|error|canceled
    progress: float = 0.0
    message: str = ""
    params_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # Completed units, so a crashed hour-long render resumes (spec 15.7).
    checkpoint_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error_str: str = ""
    eta_sec: Optional[float] = None
    parent_batch_id: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class RenderOutput(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id", index=True)
    variant: str = "16:9"
    local_path: str = ""
    duration: float = 0.0
    size_bytes: int = 0
    chapters_txt_path: str = ""
    ad_breaks_json: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)


class Secret(SQLModel, table=True):
    provider: str = Field(primary_key=True)
    value_encrypted: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class Setting(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value_json: Any = Field(default=None, sa_column=Column(JSON))
