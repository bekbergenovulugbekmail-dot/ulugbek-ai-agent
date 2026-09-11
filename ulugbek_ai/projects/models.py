"""Project ORM model."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ulugbek_ai.core.enums import ProjectStatus
from ulugbek_ai.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from ulugbek_ai.database.types import EnumString, JSONBType, UUIDType


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A universal project the agent can act on.

    ``integrations`` holds the per-service binding for future phases, e.g.::

        {
          "github":   {"repository": "owner/repo", "default_branch": "main"},
          "railway":  {"project_id": "..."},
          "telegram": {"bot_username": "..."}
        }

    Credentials are **never** stored here — only non-secret identifiers. Secrets
    stay in the environment / a secret manager.
    """

    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_owner_status", "owner_id", "status"),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(
        String(200), unique=True, index=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ProjectStatus] = mapped_column(
        EnumString(ProjectStatus, 32), default=ProjectStatus.ACTIVE, nullable=False
    )
    repository: Mapped[str | None] = mapped_column(String(500), nullable=True)
    environment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Free-form keywords used to route a request to this project.
    keywords: Mapped[list[str]] = mapped_column(
        JSONBType, default=list, nullable=False
    )
    #: Non-secret integration bindings, keyed by service name.
    integrations: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )
