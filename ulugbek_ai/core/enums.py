"""Enumerations shared across the whole system.

These live in ``core`` so that no domain module has to import another domain
module just to reference a status value.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """``str``-backed enum so values serialize transparently to JSON/SQL."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #
class PermissionLevel(StrEnum):
    """Risk class of an action. Ordered from least to most dangerous."""

    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"
    DELETE = "DELETE"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _PERMISSION_ORDER[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, PermissionLevel):
            return NotImplemented
        return self.rank < other.rank

    def __le__(self, other: object) -> bool:
        if not isinstance(other, PermissionLevel):
            return NotImplemented
        return self.rank <= other.rank

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, PermissionLevel):
            return NotImplemented
        return self.rank > other.rank

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, PermissionLevel):
            return NotImplemented
        return self.rank >= other.rank


_PERMISSION_ORDER: dict[PermissionLevel, int] = {
    PermissionLevel.READ: 0,
    PermissionLevel.WRITE: 1,
    PermissionLevel.EXECUTE: 2,
    PermissionLevel.DELETE: 3,
    PermissionLevel.CRITICAL: 4,
}


class PermissionMode(StrEnum):
    """How a permission level is handled by the policy."""

    AUTO = "auto"           # execute immediately
    APPROVAL = "approval"   # pause the run and request human approval
    DENY = "deny"           # never execute


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
class TaskStatus(StrEnum):
    PENDING = "PENDING"
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_TASK_STATUSES


_TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)


class TaskPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class StepStatus(StrEnum):
    """Status of a single planned step inside a task."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
class MemoryType(StrEnum):
    USER_CONTEXT = "USER_CONTEXT"
    PROJECT_CONTEXT = "PROJECT_CONTEXT"
    DECISION = "DECISION"
    PREFERENCE = "PREFERENCE"
    FACT = "FACT"
    TASK_CONTEXT = "TASK_CONTEXT"
    CONVERSATION_SUMMARY = "CONVERSATION_SUMMARY"
    TOOL_RESULT = "TOOL_RESULT"


# --------------------------------------------------------------------------- #
# Agent runs
# --------------------------------------------------------------------------- #
class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepType(StrEnum):
    """Audit trace entry types (see ``agent.models.AgentStep``)."""

    AGENT_RUN = "agent_run"
    TASK = "task"
    PLAN = "plan"
    REPLAN = "replan"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    PERMISSION = "permission"
    APPROVAL = "approval"
    VERIFICATION = "verification"
    ERROR = "error"
    FINAL_RESULT = "final_result"


# --------------------------------------------------------------------------- #
# Tool execution / approvals
# --------------------------------------------------------------------------- #
class ToolExecutionStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    DENIED = "DENIED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class VerificationStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    INCONCLUSIVE = "INCONCLUSIVE"
    SKIPPED = "SKIPPED"
