"""Agent run and trace ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ulugbek_ai.core.enums import RunStatus, StepType
from ulugbek_ai.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from ulugbek_ai.database.types import DateTimeTZ, EnumString, JSONBType, UUIDType


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One invocation of the agent loop.

    ``transcript`` holds the serialized LLM conversation. Persisting it is what
    makes a run paused on an approval resumable — including across a process
    restart or a different worker picking it up.
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_status_created", "status", "created_at"),
    )

    input: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        EnumString(RunStatus, 20),
        default=RunStatus.RUNNING,
        nullable=False,
        index=True,
    )
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    iterations: Mapped[int] = mapped_column(default=0, nullable=False)
    replans: Mapped[int] = mapped_column(default=0, nullable=False)
    #: Serialized ``list[LLMMessage]``.
    transcript: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONBType, default=list, nullable=False
    )
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_usage: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )

    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)


class AgentStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One entry of a run's execution trace.

    Written exclusively through
    :class:`~ulugbek_ai.observability.audit.AuditLogger`, which redacts payloads.
    """

    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("agent_run_id", "sequence", name="uq_agent_steps_run_sequence"),
        Index("ix_agent_steps_run_sequence", "agent_run_id", "sequence"),
    )

    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(nullable=False)
    iteration: Mapped[int | None] = mapped_column(nullable=True)
    type: Mapped[StepType] = mapped_column(
        EnumString(StepType, 30), nullable=False, index=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )
    success: Mapped[bool | None] = mapped_column(nullable=True)
