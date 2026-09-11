"""Exception hierarchy.

Every error carries a machine-readable ``code`` so the API layer can translate
exceptions into stable HTTP responses without string matching.
"""

from __future__ import annotations

from typing import Any


class UlugbekError(Exception):
    """Base class for all application errors."""

    code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class ConfigurationError(UlugbekError):
    """A required dependency is missing or misconfigured.

    Reported as 503 rather than 500: the service is up, but a dependency it
    needs for this request is not wired, and the caller can retry once it is.
    """

    code = "configuration_error"
    http_status = 503


class NotFoundError(UlugbekError):
    code = "not_found"
    http_status = 404


class ValidationError(UlugbekError):
    code = "validation_error"
    http_status = 422


class ConflictError(UlugbekError):
    code = "conflict"
    http_status = 409


# --- LLM ------------------------------------------------------------------- #
class LLMError(UlugbekError):
    code = "llm_error"
    http_status = 502


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    http_status = 504


class LLMResponseFormatError(LLMError):
    """The model returned something that does not match the requested schema."""

    code = "llm_response_format_error"


# --- Tools ----------------------------------------------------------------- #
class ToolError(UlugbekError):
    code = "tool_error"


class ToolNotFoundError(ToolError, NotFoundError):
    code = "tool_not_found"
    http_status = 404


class ToolAlreadyRegisteredError(ToolError, ConflictError):
    code = "tool_already_registered"
    http_status = 409


class ToolInputError(ToolError, ValidationError):
    code = "tool_input_error"
    http_status = 422


class ToolTimeoutError(ToolError):
    code = "tool_timeout"
    http_status = 504


# --- Permissions / approvals ----------------------------------------------- #
class PermissionDeniedError(UlugbekError):
    code = "permission_denied"
    http_status = 403


class ApprovalRequiredError(UlugbekError):
    """Raised when an action cannot proceed without explicit human approval."""

    code = "approval_required"
    http_status = 202


# --- Agent ----------------------------------------------------------------- #
class AgentError(UlugbekError):
    code = "agent_error"


class MaxIterationsExceededError(AgentError):
    code = "max_iterations_exceeded"


class AgentTimeoutError(AgentError):
    code = "agent_timeout"
    http_status = 504
