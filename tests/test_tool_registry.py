"""Registry: registration, validation, permissions, timeouts, failure isolation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ulugbek_ai.core.enums import PermissionLevel
from ulugbek_ai.core.errors import (
    PermissionDeniedError,
    ToolAlreadyRegisteredError,
    ToolInputError,
    ToolNotFoundError,
)
from ulugbek_ai.tools.base import Tool, ToolContext, ToolResult
from ulugbek_ai.tools.permissions import PermissionService
from ulugbek_ai.tools.registry import ToolRegistry


class EchoTool(Tool):
    name = "echo"
    description = "Return the message it was given."
    permission = PermissionLevel.READ
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context) -> ToolResult:
        return ToolResult.success({"echo": arguments["message"]})


class ExplodingTool(Tool):
    name = "explode"
    description = "Always raises, to prove a tool cannot crash a run."
    permission = PermissionLevel.READ

    async def execute(self, arguments, context) -> ToolResult:
        raise RuntimeError("boom")


class SlowTool(Tool):
    name = "slow"
    description = "Sleeps past its timeout."
    permission = PermissionLevel.READ
    timeout_seconds = 0.05

    async def execute(self, arguments, context) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult.success("never reached")


class DangerousTool(Tool):
    name = "danger"
    description = "A CRITICAL action, gated by the permission system."
    permission = PermissionLevel.CRITICAL

    async def execute(self, arguments, context) -> ToolResult:
        return ToolResult.success("did the dangerous thing")


class LeakyTool(Tool):
    name = "leaky"
    description = "Returns a credential, to prove the registry redacts output."
    permission = PermissionLevel.READ

    async def execute(self, arguments, context) -> ToolResult:
        return ToolResult.success({"api_key": "sk-ant-0123456789abcdefgh"})


@pytest.fixture
def registry() -> ToolRegistry:
    registry = ToolRegistry(permissions=PermissionService())
    registry.register_all(
        [EchoTool(), ExplodingTool(), SlowTool(), DangerousTool(), LeakyTool()]
    )
    return registry


async def test_registers_and_executes(registry: ToolRegistry) -> None:
    result = await registry.execute("echo", {"message": "hi"}, ToolContext())
    assert result.ok
    assert result.output == {"echo": "hi"}
    assert result.duration_ms >= 0


async def test_unknown_tool(registry: ToolRegistry) -> None:
    with pytest.raises(ToolNotFoundError):
        await registry.execute("nope", {}, ToolContext())


def test_duplicate_registration_is_rejected(registry: ToolRegistry) -> None:
    with pytest.raises(ToolAlreadyRegisteredError):
        registry.register(EchoTool())
    registry.register(EchoTool(), replace=True)  # explicit replace is fine


async def test_arguments_are_validated(registry: ToolRegistry) -> None:
    with pytest.raises(ToolInputError):
        await registry.execute("echo", {}, ToolContext())
    with pytest.raises(ToolInputError):
        await registry.execute("echo", {"message": 42}, ToolContext())
    with pytest.raises(ToolInputError):
        await registry.execute("echo", {"message": "x", "extra": 1}, ToolContext())


async def test_a_raising_tool_becomes_a_failed_result(registry: ToolRegistry) -> None:
    result = await registry.execute("explode", {}, ToolContext())
    assert result.ok is False
    assert "RuntimeError" in result.error


async def test_a_slow_tool_is_timed_out(registry: ToolRegistry) -> None:
    result = await registry.execute("slow", {}, ToolContext())
    assert result.ok is False
    assert "timed out" in result.error


async def test_critical_tool_requires_approval(registry: ToolRegistry) -> None:
    with pytest.raises(PermissionDeniedError) as exc_info:
        await registry.execute("danger", {}, ToolContext())
    assert exc_info.value.details["requires_approval"] is True

    result = await registry.execute("danger", {}, ToolContext(), approved=True)
    assert result.ok


async def test_output_is_redacted(registry: ToolRegistry) -> None:
    result = await registry.execute("leaky", {}, ToolContext())
    assert result.output["api_key"] == "***REDACTED***"


async def test_long_output_is_truncated() -> None:
    class ChattyTool(Tool):
        name = "chatty"
        description = "Returns far more text than the budget allows."
        permission = PermissionLevel.READ

        async def execute(self, arguments, context) -> ToolResult:
            return ToolResult.success("x" * 10_000)

    registry = ToolRegistry(max_result_chars=100)
    registry.register(ChattyTool())
    result = await registry.execute("chatty", {}, ToolContext())
    assert len(result.output) == 100


def test_listing_and_specs(registry: ToolRegistry) -> None:
    names = [tool.name for tool in registry.list()]
    assert names == sorted(names)

    read_only = registry.list(max_permission=PermissionLevel.READ)
    assert "danger" not in [tool.name for tool in read_only]

    spec = next(s for s in registry.llm_specs() if s.name == "echo")
    assert spec.to_wire()["input_schema"]["required"] == ["message"]


def test_a_tool_must_describe_itself() -> None:
    with pytest.raises(ValueError, match="description"):

        class Nameless(Tool):
            name = "nameless"
            description = ""

            async def execute(self, arguments, context) -> ToolResult:
                return ToolResult.success(None)
