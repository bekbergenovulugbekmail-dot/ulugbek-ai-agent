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
    #: Response headers this error requires. A 401 without a challenge header
    #: is not a 401 a client can act on.
    headers: dict[str, str] | None = None

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


# --- Speech -------------------------------------------------------------- #
class SpeechError(UlugbekError):
    """A speech provider failed. 502, like the model: theirs, not ours."""

    code = "speech_error"
    http_status = 502


class SpeechTimeoutError(SpeechError):
    code = "speech_timeout"
    http_status = 504


class PayloadTooLargeError(UlugbekError):
    """The request body exceeds a limit this server set.

    Separate from :class:`ValidationError` because the fix is different: the
    body is not malformed, there is simply too much of it, and 413 is the one
    status a client can act on without reading the message.
    """

    code = "payload_too_large"
    http_status = 413


class UnsupportedMediaTypeError(UlugbekError):
    """The body is in a container this server cannot pass on."""

    code = "unsupported_media_type"
    http_status = 415


# --- Permissions / approvals ----------------------------------------------- #
class AuthenticationError(UlugbekError):
    """The caller did not prove who they are.

    Distinct from :class:`PermissionDeniedError`: this one says *who are you*,
    that one says *not you*. Collapsing them tells an attacker which resources
    exist and tells an operator nothing about which half is broken.
    """

    code = "authentication_required"
    http_status = 401
    headers = {"WWW-Authenticate": "Bearer"}


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
