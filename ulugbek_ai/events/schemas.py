"""Wire shapes for the event stream."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ulugbek_ai.core.enums import RunStatus, TaskStatus
from ulugbek_ai.events.types import AgentPhase, EventStatus, EventType


class AgentEvent(BaseModel):
    """One thing the agent did.

    ``safe_message`` is always displayable: it comes from the audit trail, which
    is redacted on write. ``metadata`` is redacted for the same reason — but it
    is still structured data, so a client should treat it as untrusted text and
    never execute it.
    """

    id: uuid.UUID
    run_id: uuid.UUID
    task_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    sequence: int
    iteration: int | None = None
    type: EventType
    status: EventStatus
    timestamp: datetime
    safe_message: str
    #: Short label for the thing acted on (a tool name, a check name).
    subject: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentStateSnapshot(BaseModel):
    """Everything a client needs to render the agent's current state."""

    phase: AgentPhase
    label: str
    busy: bool
    run_id: uuid.UUID | None = None
    run_status: RunStatus | None = None
    task_id: uuid.UUID | None = None
    task_status: TaskStatus | None = None
    goal: str | None = None
    project_id: uuid.UUID | None = None
    iteration: int = 0
    detail: str | None = None
    pending_approval_id: uuid.UUID | None = None
    updated_at: datetime | None = None


class EventPage(BaseModel):
    """A page of events plus the cursor needed to ask for the next one."""

    events: list[AgentEvent] = Field(default_factory=list)
    #: Highest sequence returned; pass back as ``after_sequence`` when polling
    #: a single run.
    cursor: int | None = None
    has_more: bool = False
