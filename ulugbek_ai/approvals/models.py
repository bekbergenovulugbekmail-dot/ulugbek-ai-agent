"""Approval ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ulugbek_ai.core.enums import ApprovalStatus, PermissionLevel
from ulugbek_ai.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from ulugbek_ai.database.types import DateTimeTZ, EnumString, JSONBType, UUIDType


class Approval(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A pending or decided request to perform a gated action.

    ``tool_arguments`` is stored redacted, because an approval row is shown to a
    human and may be exported.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_status_created", "status", "created_at"),
    )

    status: Mapped[ApprovalStatus] = mapped_column(
        EnumString(ApprovalStatus, 20),
        default=ApprovalStatus.PENDING,
        nullable=False,
        index=True,
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_arguments: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )
    permission: Mapped[PermissionLevel] = mapped_column(
        EnumString(PermissionLevel, 20), nullable=False
    )
    #: Why approval is needed, in words a human can act on.
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    #: What the agent is trying to achieve, for context.
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)

    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTimeTZ, nullable=True)

    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: The tool_use id this approval unblocks, so the resumed run replies to the
    #: exact call the model made.
    tool_call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    @property
    def is_pending(self) -> bool:
        return self.status == ApprovalStatus.PENDING
