"""Agent API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ulugbek_ai.core.enums import RunStatus, StepType, TaskStatus, VerificationStatus


class AgentRunRequest(BaseModel):
    """What the caller asks the agent to do."""

    message: str = Field(min_length=1, max_length=50_000)
    project_id: uuid.UUID | None = Field(
        default=None,
        description="Pin the run to a project. Omit to let the agent route it.",
    )
    user_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = Field(
        default=None, description="Continue an existing task instead of creating one."
    )
    max_iterations: int | None = Field(default=None, ge=1, le=50)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequestInfo(BaseModel):
    """Returned when a run pauses for a human decision."""

    approval_id: uuid.UUID
    tool_name: str
    permission: str
    reason: str
    tool_arguments: dict[str, Any]


class VerificationInfo(BaseModel):
    status: VerificationStatus
    reason: str


class AgentRunResponse(BaseModel):
    """Outcome of one agent run."""

    run_id: uuid.UUID
    task_id: uuid.UUID | None
    project_id: uuid.UUID | None
    status: RunStatus
    task_status: TaskStatus | None
    output: str | None
    error: str | None
    iterations: int
    replans: int
    tools_used: list[str] = Field(default_factory=list)
    verification: VerificationInfo | None = None
    approval: ApprovalRequestInfo | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)

    @property
    def needs_approval(self) -> bool:
        return self.status == RunStatus.WAITING_APPROVAL


class AgentStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sequence: int
    iteration: int | None
    type: StepType
    summary: str
    payload: dict[str, Any]
    success: bool | None
    created_at: datetime


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    input: str
    status: RunStatus
    output: str | None
    error: str | None
    iterations: int
    replans: int
    model: str | None
    token_usage: dict[str, Any]
    task_id: uuid.UUID | None
    project_id: uuid.UUID | None
    user_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class AgentRunDetail(AgentRunRead):
    """A run plus its full execution trace."""

    steps: list[AgentStepRead] = Field(default_factory=list)
