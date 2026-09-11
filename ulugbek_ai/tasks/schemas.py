"""Task and plan schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ulugbek_ai.core.enums import StepStatus, TaskPriority, TaskStatus


class PlanStep(BaseModel):
    """One step of a plan.

    ``tool`` is a *hint* from the planner, not a binding decision: the executor
    still picks the tool and the registry still enforces permissions.
    """

    index: int = Field(ge=0)
    description: str = Field(min_length=1, max_length=2_000)
    tool: str | None = Field(default=None, max_length=100)
    expected_outcome: str | None = Field(default=None, max_length=2_000)
    status: StepStatus = StepStatus.PENDING
    notes: str | None = Field(default=None, max_length=4_000)

    def mark(self, status: StepStatus, *, notes: str | None = None) -> "PlanStep":
        return self.model_copy(update={"status": status, "notes": notes})


class Plan(BaseModel):
    """A planner's output."""

    goal_restatement: str = Field(default="", max_length=4_000)
    steps: list[PlanStep] = Field(default_factory=list)
    requires_tools: bool = False
    reasoning: str | None = Field(default=None, max_length=8_000)

    @property
    def is_empty(self) -> bool:
        return not self.steps


class TaskCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=20_000)
    priority: TaskPriority = TaskPriority.NORMAL
    project_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    parent_task_id: uuid.UUID | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    goal: str
    status: TaskStatus
    priority: TaskPriority
    steps: list[dict[str, Any]]
    current_step: int
    result: str | None
    error: str | None
    extra: dict[str, Any]
    project_id: uuid.UUID | None
    user_id: uuid.UUID | None
    parent_task_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
