"""Task ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from ulugbek_ai.core.enums import TaskPriority, TaskStatus
from ulugbek_ai.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from ulugbek_ai.database.types import DateTimeTZ, EnumString, JSONBType, UUIDType


class Task(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A goal plus the plan and progress for reaching it.

    ``steps`` is the serialized plan — a list of
    :class:`~ulugbek_ai.tasks.schemas.PlanStep` dicts — and ``current_step`` is
    the index the executor is working on.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_status_created", "status", "created_at"),
        Index("ix_tasks_project_status", "project_id", "status"),
    )

    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[TaskStatus] = mapped_column(
        EnumString(TaskStatus, 32),
        default=TaskStatus.PENDING,
        nullable=False,
        index=True,
    )
    priority: Mapped[TaskPriority] = mapped_column(
        EnumString(TaskPriority, 16), default=TaskPriority.NORMAL, nullable=False
    )
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBType, default=list, nullable=False
    )
    current_step: Mapped[int] = mapped_column(default=0, nullable=False)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)
