"""Memory API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ulugbek_ai.core.enums import MemoryType


class MemoryCreate(BaseModel):
    type: MemoryType
    content: str = Field(min_length=1, max_length=50_000)
    summary: str | None = Field(default=None, max_length=5_000)
    source: str | None = Field(default=None, max_length=120)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
    user_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=50_000)
    summary: str | None = Field(default=None, max_length=5_000)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    tags: list[str] | None = None
    extra: dict[str, Any] | None = None


class MemoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: MemoryType
    content: str
    summary: str | None
    source: str | None
    importance: float
    tags: list[str]
    extra: dict[str, Any]
    user_id: uuid.UUID | None
    project_id: uuid.UUID | None
    task_id: uuid.UUID | None
    access_count: int
    last_accessed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MemorySearchResult(BaseModel):
    """A memory plus the score that retrieved it."""

    memory: MemoryRead
    score: float
