"""Approval API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ulugbek_ai.core.enums import ApprovalStatus, PermissionLevel


class ApprovalDecision(BaseModel):
    """Body of an approve/reject call."""

    decided_by: str | None = Field(default=None, max_length=255)
    note: str | None = Field(default=None, max_length=2_000)


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: ApprovalStatus
    tool_name: str
    tool_arguments: dict[str, Any]
    permission: PermissionLevel
    reason: str
    goal: str | None
    decided_by: str | None
    decision_note: str | None
    decided_at: datetime | None
    expires_at: datetime | None
    agent_run_id: uuid.UUID | None
    task_id: uuid.UUID | None
    project_id: uuid.UUID | None
    requested_by: uuid.UUID | None
    tool_call_id: str | None
    created_at: datetime
    updated_at: datetime
