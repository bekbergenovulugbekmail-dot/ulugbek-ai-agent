"""Project audit rows into client-facing events.

This is a pure function over an :class:`~ulugbek_ai.agent.models.AgentStep`, so
it is trivially testable and has no database or transport concerns.
"""

from __future__ import annotations

from typing import Any, Callable

from ulugbek_ai.agent.models import AgentStep
from ulugbek_ai.core.enums import StepType
from ulugbek_ai.events.schemas import AgentEvent
from ulugbek_ai.events.types import EventStatus, EventType

#: Trace entries that carry no useful signal for a user interface.
HIDDEN_STEP_TYPES: frozenset[StepType] = frozenset({StepType.TASK})


def _tool_result_type(step: AgentStep) -> EventType:
    return EventType.TOOL_COMPLETED if step.success else EventType.TOOL_FAILED


def _verification_type(step: AgentStep) -> EventType:
    return (
        EventType.VERIFICATION_COMPLETED
        if step.success
        else EventType.VERIFICATION_FAILED
    )


def _final_type(step: AgentStep) -> EventType:
    return EventType.TASK_COMPLETED if step.success else EventType.TASK_FAILED


#: ``StepType`` -> ``EventType``. A callable entry decides from the row itself.
_TYPE_MAP: dict[StepType, EventType | Callable[[AgentStep], EventType]] = {
    StepType.AGENT_STARTED: EventType.AGENT_STARTED,
    StepType.AGENT_RUN: EventType.CONTEXT_LOADED,
    StepType.PLAN: EventType.AGENT_PLANNING,
    StepType.REPLAN: EventType.AGENT_REPLANNING,
    StepType.LLM_CALL: EventType.AGENT_THINKING,
    StepType.TOOL_CALL: EventType.TOOL_STARTED,
    StepType.TOOL_RESULT: _tool_result_type,
    StepType.PERMISSION: EventType.PERMISSION_CHECKED,
    StepType.APPROVAL: EventType.APPROVAL_REQUIRED,
    StepType.VERIFICATION_STARTED: EventType.VERIFICATION_STARTED,
    StepType.VERIFICATION: _verification_type,
    StepType.ERROR: EventType.AGENT_ERROR,
    StepType.FINAL_RESULT: _final_type,
}

#: ``EventType`` -> how a client should render it.
_STATUS_MAP: dict[EventType, EventStatus] = {
    EventType.AGENT_STARTED: EventStatus.INFO,
    EventType.CONTEXT_LOADED: EventStatus.SUCCESS,
    EventType.AGENT_PLANNING: EventStatus.RUNNING,
    EventType.AGENT_REPLANNING: EventStatus.RUNNING,
    EventType.AGENT_THINKING: EventStatus.RUNNING,
    EventType.TOOL_STARTED: EventStatus.RUNNING,
    EventType.TOOL_COMPLETED: EventStatus.SUCCESS,
    EventType.TOOL_FAILED: EventStatus.FAILURE,
    EventType.PERMISSION_CHECKED: EventStatus.INFO,
    EventType.APPROVAL_REQUIRED: EventStatus.WAITING,
    EventType.VERIFICATION_STARTED: EventStatus.RUNNING,
    EventType.VERIFICATION_COMPLETED: EventStatus.SUCCESS,
    EventType.VERIFICATION_FAILED: EventStatus.FAILURE,
    EventType.TASK_COMPLETED: EventStatus.SUCCESS,
    EventType.TASK_FAILED: EventStatus.FAILURE,
    EventType.AGENT_ERROR: EventStatus.FAILURE,
}

#: Payload keys worth forwarding, per event family. Everything else stays in the
#: audit trail — the stream should carry what a UI renders, not the whole row.
_METADATA_KEYS: tuple[str, ...] = (
    "tool",
    "call_id",
    "approval_id",
    "permission",
    "status",
    "reason",
    "steps",
    "duration_ms",
    "memory_count",
    "usage",
    "requires_approval",
)


def _subject(step: AgentStep, payload: dict[str, Any]) -> str | None:
    """The thing this event is about — a tool name, a check, a verdict."""
    if isinstance(payload.get("tool"), str):
        return payload["tool"]
    if step.type is StepType.VERIFICATION and isinstance(payload.get("source"), str):
        return payload["source"]
    return None


def _metadata(step: AgentStep) -> dict[str, Any]:
    payload = step.payload if isinstance(step.payload, dict) else {}
    metadata = {
        key: payload[key] for key in _METADATA_KEYS if key in payload
    }
    # A tool result is summarized rather than forwarded whole: a client shows the
    # outcome and the duration, not the payload.
    result = payload.get("result")
    if isinstance(result, dict):
        metadata["ok"] = result.get("ok")
        if result.get("duration_ms") is not None:
            metadata["duration_ms"] = result["duration_ms"]
        if result.get("error"):
            metadata["error"] = result["error"]
    verification = payload.get("verification")
    if isinstance(verification, dict):
        metadata["verification"] = {
            "status": verification.get("status"),
            "reason": verification.get("reason"),
        }
    if isinstance(payload.get("steps"), list):
        metadata["step_count"] = len(payload["steps"])
        metadata.pop("steps", None)
    return metadata


def project_step(
    step: AgentStep, *, project_id: Any | None = None
) -> AgentEvent | None:
    """Convert one audit row into an event, or ``None`` if it is not renderable."""
    step_type = StepType(step.type)
    if step_type in HIDDEN_STEP_TYPES:
        return None

    mapped = _TYPE_MAP.get(step_type)
    if mapped is None:
        return None
    event_type = mapped(step) if callable(mapped) else mapped

    payload = step.payload if isinstance(step.payload, dict) else {}
    return AgentEvent(
        id=step.id,
        run_id=step.agent_run_id,
        task_id=step.task_id,
        project_id=project_id,
        sequence=step.sequence,
        iteration=step.iteration,
        type=event_type,
        status=_STATUS_MAP.get(event_type, EventStatus.INFO),
        timestamp=step.created_at,
        safe_message=step.summary,
        subject=_subject(step, payload),
        metadata=_metadata(step),
    )


def project_steps(
    steps: list[AgentStep], *, project_id: Any | None = None
) -> list[AgentEvent]:
    """Convert many rows, dropping the ones that are not renderable."""
    events = (project_step(step, project_id=project_id) for step in steps)
    return [event for event in events if event is not None]
