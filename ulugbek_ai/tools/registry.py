"""Tool registry — the single place tools are looked up and invoked.

Every invocation goes through :meth:`ToolRegistry.execute`, which uniformly:

1. resolves the tool,
2. validates arguments against its ``input_schema``,
3. enforces the permission policy,
4. runs it under a timeout,
5. converts any exception into a failed :class:`ToolResult` (never a crash),
6. redacts and truncates the output.

Because all of that lives here, a new integration only has to implement
:class:`~ulugbek_ai.tools.base.Tool`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError

from ulugbek_ai.core.enums import PermissionLevel
from ulugbek_ai.core.errors import (
    PermissionDeniedError,
    ToolAlreadyRegisteredError,
    ToolInputError,
    ToolNotFoundError,
)
from ulugbek_ai.core.redaction import redact, truncate
from ulugbek_ai.llm.base import LLMToolSpec
from ulugbek_ai.tools.base import Tool, ToolContext, ToolResult, ToolVerification
from ulugbek_ai.tools.permissions import PermissionDecision, PermissionService

logger = logging.getLogger(__name__)

DEFAULT_TOOL_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RESULT_CHARS = 8_000


class ToolRegistry:
    """Holds the available tools and executes them safely."""

    def __init__(
        self,
        *,
        permissions: PermissionService | None = None,
        default_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self._permissions = permissions or PermissionService()
        self._default_timeout = default_timeout_seconds
        self._max_result_chars = max_result_chars

    # -------------------------------------------------------------- registry #
    def register(self, tool: Tool, *, replace: bool = False) -> Tool:
        """Add a tool. Duplicate names are an error unless ``replace=True``."""
        if tool.name in self._tools and not replace:
            raise ToolAlreadyRegisteredError(
                f"A tool named '{tool.name}' is already registered.",
                details={"tool": tool.name},
            )
        Draft202012Validator.check_schema(tool.input_schema)
        self._tools[tool.name] = tool
        return tool

    def register_all(self, tools: Iterable[Tool], *, replace: bool = False) -> None:
        for tool in tools:
            self.register(tool, replace=replace)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFoundError(
                f"No tool named '{name}' is registered.",
                details={"tool": name, "available": sorted(self._tools)},
            )
        return tool

    def has(self, name: str) -> bool:
        return name in self._tools

    def list(
        self, *, max_permission: PermissionLevel | None = None
    ) -> list[Tool]:
        """Registered tools, optionally capped at a permission level."""
        tools = sorted(self._tools.values(), key=lambda tool: tool.name)
        if max_permission is not None:
            tools = [tool for tool in tools if tool.permission <= max_permission]
        return tools

    def describe_all(self) -> list[dict[str, Any]]:
        return [tool.describe() for tool in self.list()]

    def llm_specs(
        self, *, max_permission: PermissionLevel | None = None
    ) -> list[LLMToolSpec]:
        """Tool specifications to advertise to the model."""
        return [tool.to_llm_spec() for tool in self.list(max_permission=max_permission)]

    @property
    def permissions(self) -> PermissionService:
        return self._permissions

    # ------------------------------------------------------------ validation #
    @staticmethod
    def validate_arguments(tool: Tool, arguments: dict[str, Any]) -> None:
        """Check *arguments* against the tool's input schema."""
        try:
            Draft202012Validator(tool.input_schema).validate(arguments)
        except SchemaValidationError as exc:
            path = "/".join(str(part) for part in exc.absolute_path) or "<root>"
            raise ToolInputError(
                f"Invalid arguments for tool '{tool.name}' at {path}: {exc.message}",
                details={"tool": tool.name, "path": path},
            ) from exc

    def check_permission(
        self, tool: Tool, *, approved: bool = False
    ) -> PermissionDecision:
        """Evaluate the policy for *tool* without executing it."""
        return self._permissions.check(
            tool.permission, tool_name=tool.name, approved=approved
        )

    # --------------------------------------------------------------- execute #
    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
        *,
        approved: bool = False,
    ) -> ToolResult:
        """Validate, authorize and run a tool.

        Raises:
            ToolNotFoundError: unknown tool.
            ToolInputError: arguments do not match the schema.
            PermissionDeniedError: the policy denies or gates this action. The
                caller inspects ``details['requires_approval']`` to tell an
                outright denial from a pending approval.
        """
        tool = self.get(name)
        self.validate_arguments(tool, arguments)

        decision = self.check_permission(tool, approved=approved)
        if not decision.allowed:
            raise PermissionDeniedError(
                decision.reason,
                details={
                    "tool": tool.name,
                    "requires_approval": decision.requires_approval,
                    **decision.to_dict(),
                },
            )

        timeout = tool.timeout_seconds or self._default_timeout
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                tool.execute(arguments, context), timeout=timeout
            )
        except asyncio.TimeoutError:
            elapsed = int((time.perf_counter() - started) * 1000)
            logger.warning("Tool %s timed out after %.1fs", tool.name, timeout)
            return ToolResult(
                ok=False,
                error=f"Tool '{tool.name}' timed out after {timeout:.1f}s.",
                duration_ms=elapsed,
                evidence={"timeout_seconds": timeout},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a tool must never crash the run
            elapsed = int((time.perf_counter() - started) * 1000)
            logger.exception("Tool %s raised", tool.name)
            return ToolResult(
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=elapsed,
            )

        result.duration_ms = result.duration_ms or int(
            (time.perf_counter() - started) * 1000
        )
        return self._sanitize(result)

    async def verify(
        self,
        name: str,
        arguments: dict[str, Any],
        result: ToolResult,
        context: ToolContext,
    ) -> ToolVerification:
        """Run a tool's own post-condition check, never raising."""
        tool = self.get(name)
        try:
            return await tool.verify(arguments, result, context)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Verification for tool %s raised", name)
            return ToolVerification.failure(f"verification error: {exc}")

    def _sanitize(self, result: ToolResult) -> ToolResult:
        """Redact secrets and cap the size of whatever the tool returned."""
        result.output = redact(result.output)
        if isinstance(result.output, str):
            result.output = truncate(result.output, self._max_result_chars)
        if result.error:
            result.error = truncate(
                str(redact(result.error)), self._max_result_chars
            )
        result.evidence = redact(result.evidence)
        return result


def build_default_registry(
    *,
    permissions: PermissionService | None = None,
    default_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS,
) -> ToolRegistry:
    """Registry pre-loaded with the built-in tools.

    Phase 1 ships only safe, local tools. External integrations (GitHub,
    Railway, Telegram, Instagram, browser, ERP) plug in here in later phases.
    """
    from ulugbek_ai.tools.builtin import default_tools

    registry = ToolRegistry(
        permissions=permissions,
        default_timeout_seconds=default_timeout_seconds,
        max_result_chars=max_result_chars,
    )
    registry.register_all(default_tools())
    return registry
