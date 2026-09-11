"""Project API schemas."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ulugbek_ai.core.enums import ProjectStatus

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Normalize a name into a URL-safe slug."""
    return _SLUG_RE.sub("-", value.strip().lower()).strip("-") or "project"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    slug: str | None = Field(default=None, max_length=200)
    status: ProjectStatus = ProjectStatus.ACTIVE
    repository: str | None = Field(default=None, max_length=500)
    environment: str | None = Field(default=None, max_length=100)
    keywords: list[str] = Field(default_factory=list)
    integrations: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("slug")
    @classmethod
    def _normalize_slug(cls, value: str | None) -> str | None:
        return slugify(value) if value else None

    @field_validator("keywords")
    @classmethod
    def _normalize_keywords(cls, value: list[str]) -> list[str]:
        return [keyword.strip().lower() for keyword in value if keyword.strip()]


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10_000)
    status: ProjectStatus | None = None
    repository: str | None = Field(default=None, max_length=500)
    environment: str | None = Field(default=None, max_length=100)
    keywords: list[str] | None = None
    integrations: dict[str, Any] | None = None
    extra: dict[str, Any] | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    status: ProjectStatus
    repository: str | None
    environment: str | None
    owner_id: uuid.UUID | None
    keywords: list[str]
    integrations: dict[str, Any]
    extra: dict[str, Any]
    created_at: datetime
    updated_at: datetime
