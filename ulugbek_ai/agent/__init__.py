"""The agent itself.

The loop is deliberately explicit rather than a thin wrapper around a chat call:

    UNDERSTAND -> LOAD CONTEXT -> PLAN -> SELECT TOOL -> EXECUTE -> OBSERVE
              -> REASON -> VERIFY -> COMPLETE
                                  \\-> REPLAN -> EXECUTE -> VERIFY

Each stage is a separate, testable collaborator: :class:`ContextBuilder`,
:class:`Planner`, :class:`Executor`, :class:`Verifier`, orchestrated by
:class:`AgentEngine`.
"""

from ulugbek_ai.agent.context import ContextBuilder, RunContext
from ulugbek_ai.agent.engine import AgentEngine
from ulugbek_ai.agent.executor import Executor
from ulugbek_ai.agent.models import AgentRun, AgentStep
from ulugbek_ai.agent.planner import Planner
from ulugbek_ai.agent.verifier import Verifier

__all__ = [
    "AgentEngine",
    "AgentRun",
    "AgentStep",
    "ContextBuilder",
    "Executor",
    "Planner",
    "RunContext",
    "Verifier",
]
