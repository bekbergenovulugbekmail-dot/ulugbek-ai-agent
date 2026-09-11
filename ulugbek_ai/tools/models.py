"""Tool execution audit rows."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ulugbek_ai.core.enums import (
    PermissionLevel,
    ToolExecutionStatus,
    VerificationStatus,
)
from ulugbek_ai.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from ulugbek_ai.database.types import DateTimeTZ, EnumString, JSONBType, UUIDType


class ToolExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One recorded tool invocation.

    Inputs and outputs are stored **redacted** — see
    :mod:`ulugbek_ai.core.redaction`. This table is the answer to "what did the
    agent actually do, and did it work?".
    """

    __tablename__ = "tool_executions"
    __table_args__ = (
        Index("ix_tool_executions_run_created", "agent_run_id", "created_at"),
        Index("ix_tool_executions_tool_status", "tool_name", "status"),
    )

    tool_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    #: Provider the tool spoke to; NULL for a local tool.
    service: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    status: Mapped[ToolExecutionStatus] = mapped_column(
        EnumString(ToolExecutionStatus, 20), nullable=False
    )
    permission: Mapped[PermissionLevel] = mapped_column(
        EnumString(PermissionLevel, 20), nullable=False
    )
    arguments: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONBType, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(default=0, nullable=False)

    verification_status: Mapped[VerificationStatus | None] = mapped_column(
        EnumString(VerificationStatus, 20), nullable=True
    )
    verification_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)
