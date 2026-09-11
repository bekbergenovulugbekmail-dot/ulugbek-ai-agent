"""Tool interface.

Contract for every tool, present and future:

* ``name`` / ``description`` — what the model sees.
* ``input_schema`` / ``output_schema`` — JSON Schema, validated both ways.
* ``permission`` — risk class, enforced before ``execute`` is ever called.
* ``timeout_seconds`` — hard ceiling applied by the registry.
* ``execute`` — the actual work; may raise, the registry converts failures into
  a failed :class:`ToolResult`.
* ``verify`` — optional post-condition check, so a tool can prove it worked
  rather than being assumed successful.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import PermissionLevel, VerificationStatus
from ulugbek_ai.core.redaction import redact
from ulugbek_ai.llm.base import LLMToolSpec


@dataclass(slots=True)
class ToolContext:
    """Everything a tool may need about the run that invoked it.

    Passing a context object (rather than wiring globals) is what lets a tool
    query the same transaction as the agent and stay unit-testable.
    """

    session: AsyncSession | None = None
    run_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResult:
    """Outcome of one tool invocation."""

    ok: bool
    output: Any = None
    error: str | None = None
    duration_ms: int = 0
    #: Free-form facts a verifier can check (ids, counts, urls, ...).
    evidence: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(cls, output: Any, **evidence: Any) -> "ToolResult":
        return cls(ok=True, output=output, evidence=evidence)

    @classmethod
    def failure(cls, error: str, **evidence: Any) -> "ToolResult":
        return cls(ok=False, error=error, evidence=evidence)

    def to_dict(self) -> dict[str, Any]:
        """Redacted, JSON-safe representation for transcripts and audit rows."""
        return redact(
            {
                "ok": self.ok,
                "output": self.output,
                "error": self.error,
                "duration_ms": self.duration_ms,
                "evidence": self.evidence,
            }
        )


@dataclass(slots=True)
class ToolVerification:
    """Result of a tool's own post-condition check."""

    status: VerificationStatus
    reason: str = ""

    @classmethod
    def success(cls, reason: str = "") -> "ToolVerification":
        return cls(status=VerificationStatus.SUCCESS, reason=reason)

    @classmethod
    def failure(cls, reason: str) -> "ToolVerification":
        return cls(status=VerificationStatus.FAILURE, reason=reason)

    @classmethod
    def skipped(cls, reason: str = "tool declares no post-condition") -> "ToolVerification":
        return cls(status=VerificationStatus.SKIPPED, reason=reason)


def _has_abstract_methods(cls: type) -> bool:
    """Is *cls* still abstract?

    ``__init_subclass__`` runs before ``ABCMeta`` fills in
    ``__abstractmethods__``, so that attribute cannot be trusted here. Instead
    walk the MRO for names declared abstract anywhere and ask whether the
    version visible on *cls* is still abstract — which is exactly what makes an
    intermediate base (one that adds its own abstract method, such as a shared
    integration base) exempt from the name/description rule.
    """
    for base in cls.__mro__:
        for attribute in vars(base):
            visible = getattr(cls, attribute, None)
            if getattr(visible, "__isabstractmethod__", False):
                return True
    return False


class Tool(ABC):
    """Base class for every tool."""

    #: Unique, snake_case. This is what the model calls.
    name: str = ""
    #: Written for the model: say what it does and when to use it.
    description: str = ""
    #: JSON Schema for arguments.
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    #: JSON Schema for ``ToolResult.output``. Advisory; used for documentation
    #: and for the verifier's structural check.
    output_schema: dict[str, Any] = {}
    #: Risk class. Drives the permission and approval systems.
    permission: PermissionLevel = PermissionLevel.READ
    #: External service this tool speaks to ("github", "railway", …), or None
    #: for a local tool. Purely descriptive: it lets one card in the UI render
    #: every integration without the UI knowing any of them by name.
    service: str | None = None
    #: Per-tool timeout; ``None`` means "use the configured default".
    timeout_seconds: float | None = None
    #: Tools that mutate external state are never retried automatically.
    idempotent: bool = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not _has_abstract_methods(cls):
            if not cls.name:
                raise ValueError(f"{cls.__name__} must define a non-empty name.")
            if not cls.description:
                raise ValueError(
                    f"{cls.__name__} must define a description (the model reads it)."
                )

    @abstractmethod
    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        """Run the tool.

        Arguments have already been validated against ``input_schema`` and the
        permission check has already passed.
        """

    async def verify(
        self,
        arguments: dict[str, Any],
        result: ToolResult,
        context: ToolContext,
    ) -> ToolVerification:
        """Confirm the tool actually achieved its effect.

        The default only reports transport-level success. Tools with an
        externally observable effect (a commit landed, a deploy went live, a
        message was delivered) should override this and re-read the target.
        """
        if result.ok:
            return ToolVerification.skipped()
        return ToolVerification.failure(result.error or "tool reported failure")

    def to_llm_spec(self) -> LLMToolSpec:
        """Describe this tool to the model."""
        return LLMToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )

    def describe(self) -> dict[str, Any]:
        """Machine-readable description, used by the API and the audit trail."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "permission": self.permission.value,
            "service": self.service,
            "timeout_seconds": self.timeout_seconds,
            "idempotent": self.idempotent,
        }
