"""Memory ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ulugbek_ai.core.enums import MemoryType
from ulugbek_ai.core.utils import utcnow
from ulugbek_ai.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from ulugbek_ai.database.types import DateTimeTZ, EnumString, JSONBType, UUIDType


class Memory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One retrievable unit of knowledge.

    Scope is expressed by the nullable ``user_id`` / ``project_id`` / ``task_id``
    columns: a row with no project is global, a row with a project is scoped to
    it. ``importance`` (0..1) and ``last_accessed_at`` feed retrieval ranking.

    ``embedding`` is reserved for a future vector-search strategy; the keyword
    strategy shipped today ignores it.
    """

    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_scope", "project_id", "type"),
        Index("ix_memories_user_type", "user_id", "type"),
    )

    type: Mapped[MemoryType] = mapped_column(
        EnumString(MemoryType, 40), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    importance: Mapped[float] = mapped_column(default=0.5, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSONBType, default=list, nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )
    embedding: Mapped[list[float] | None] = mapped_column(JSONBType, nullable=True)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )

    access_count: Mapped[int] = mapped_column(default=0, nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTimeTZ, nullable=True
    )

    def touch(self) -> None:
        """Record that this memory was retrieved into a context window."""
        self.access_count += 1
        self.last_accessed_at = utcnow()
