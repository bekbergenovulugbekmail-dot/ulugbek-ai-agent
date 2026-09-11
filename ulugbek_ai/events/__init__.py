"""Universal agent event stream.

The agent already writes a complete, redacted execution trace to ``agent_steps``
(see :mod:`ulugbek_ai.observability.audit`). Rather than duplicating that into a
second table, this package **projects** those rows into a stable, transport-
agnostic event shape that a UI — or, later, a WebSocket fan-out or a webhook —
can consume without knowing anything about the database.

That keeps exactly one source of truth for "what the agent did".
"""

from ulugbek_ai.events.projector import project_step, project_steps
from ulugbek_ai.events.schemas import AgentEvent, AgentStateSnapshot
from ulugbek_ai.events.service import EventService
from ulugbek_ai.events.types import AgentPhase, EventStatus, EventType

__all__ = [
    "AgentEvent",
    "AgentPhase",
    "AgentStateSnapshot",
    "EventService",
    "EventStatus",
    "EventType",
    "project_step",
    "project_steps",
]
