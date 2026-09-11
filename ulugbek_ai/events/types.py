"""Event and agent-phase vocabularies.

These strings are a **public contract** with any client that consumes the
stream. Add to them freely; do not rename or remove a member without versioning
the stream.
"""

from __future__ import annotations

from ulugbek_ai.core.enums import StrEnum


class EventType(StrEnum):
    """What happened, in terms a user interface can render."""

    AGENT_STARTED = "agent.started"
    AGENT_PLANNING = "agent.planning"
    AGENT_REPLANNING = "agent.replanning"
    AGENT_THINKING = "agent.thinking"
    AGENT_ERROR = "agent.error"

    CONTEXT_LOADED = "context.loaded"

    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"

    PERMISSION_CHECKED = "permission.checked"
    APPROVAL_REQUIRED = "approval.required"

    VERIFICATION_STARTED = "verification.started"
    VERIFICATION_COMPLETED = "verification.completed"
    VERIFICATION_FAILED = "verification.failed"

    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"


class EventStatus(StrEnum):
    """How the event should be rendered."""

    INFO = "info"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    WAITING = "waiting"


class AgentPhase(StrEnum):
    """Coarse agent state, shown as the headline status in a client.

    This is what the operator sees instead of an opaque spinner: it always names
    the concrete thing the agent is doing.
    """

    IDLE = "IDLE"
    UNDERSTANDING = "UNDERSTANDING"
    LOADING_CONTEXT = "LOADING_CONTEXT"
    PLANNING = "PLANNING"
    THINKING = "THINKING"
    RUNNING = "RUNNING"
    USING_TOOL = "USING_TOOL"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: Human-readable label per phase — the client may override, but this keeps the
#: wording consistent across every surface.
PHASE_LABELS: dict[AgentPhase, str] = {
    AgentPhase.IDLE: "Idle",
    AgentPhase.UNDERSTANDING: "Understanding request",
    AgentPhase.LOADING_CONTEXT: "Loading context",
    AgentPhase.PLANNING: "Planning",
    AgentPhase.THINKING: "Reasoning",
    AgentPhase.RUNNING: "Working",
    AgentPhase.USING_TOOL: "Using a tool",
    AgentPhase.WAITING_APPROVAL: "Waiting for your approval",
    AgentPhase.VERIFYING: "Verifying the result",
    AgentPhase.COMPLETED: "Completed",
    AgentPhase.FAILED: "Failed",
    AgentPhase.CANCELLED: "Cancelled",
}
