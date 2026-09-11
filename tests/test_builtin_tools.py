"""Built-in tools, including the safety properties of the calculator."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import MemoryType, VerificationStatus
from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.schemas import ProjectCreate
from ulugbek_ai.tools.base import ToolContext
from ulugbek_ai.tools.builtin import (
    CalculatorTool,
    ClockTool,
    MemorySearchTool,
    MemoryWriteTool,
    ProjectListTool,
)


async def test_clock_returns_now() -> None:
    result = await ClockTool().execute({}, ToolContext())
    assert result.ok
    assert result.output["timezone"] == "UTC"
    assert "T" in result.output["iso"]


async def test_clock_rejects_an_unknown_timezone() -> None:
    result = await ClockTool().execute({"timezone": "Mars/Olympus"}, ToolContext())
    assert result.ok is False


async def test_calculator_evaluates() -> None:
    result = await CalculatorTool().execute({"expression": "(12 + 5) * 3"}, ToolContext())
    assert result.output["result"] == 51


@pytest.mark.parametrize(
    "expression",
    [
        '__import__("os").system("echo pwned")',
        "open('/etc/passwd').read()",
        "().__class__.__bases__",
        "eval('1+1')",
        "2 ** 10000000",
        "undefined_name + 1",
    ],
)
async def test_calculator_refuses_anything_but_arithmetic(expression: str) -> None:
    """No name lookup, call, attribute access or resource blow-up gets through."""
    result = await CalculatorTool().execute({"expression": expression}, ToolContext())
    assert result.ok is False


async def test_calculator_reports_division_by_zero() -> None:
    result = await CalculatorTool().execute({"expression": "1/0"}, ToolContext())
    assert result.ok is False
    assert "zero" in result.error.lower()


async def test_memory_write_then_search(session: AsyncSession) -> None:
    context = ToolContext(session=session)

    written = await MemoryWriteTool().execute(
        {
            "content": "The operator prefers deployment reports in Uzbek.",
            "type": MemoryType.PREFERENCE.value,
            "importance": 0.9,
            "tags": ["language"],
        },
        context,
    )
    assert written.ok

    found = await MemorySearchTool().execute(
        {"query": "deployment reports language"}, context
    )
    assert found.ok
    assert found.output["count"] >= 1
    assert "Uzbek" in found.output["memories"][0]["content"]


async def test_memory_write_verifies_the_row_landed(session: AsyncSession) -> None:
    """A WRITE tool proves its effect instead of assuming it."""
    tool = MemoryWriteTool()
    context = ToolContext(session=session)
    arguments = {"content": "Railway project id is rw-123.", "type": MemoryType.FACT.value}

    result = await tool.execute(arguments, context)
    verification = await tool.verify(arguments, result, context)

    assert verification.status is VerificationStatus.SUCCESS


async def test_memory_tools_need_a_session() -> None:
    result = await MemorySearchTool().execute({"query": "x"}, ToolContext())
    assert result.ok is False
    assert "session" in result.error


async def test_project_list(session: AsyncSession) -> None:
    await ProjectManager(session).create(
        ProjectCreate(name="ERP", description="Warehouse system")
    )
    result = await ProjectListTool().execute({}, ToolContext(session=session))

    assert result.ok
    assert result.output["count"] == 1
    assert result.output["projects"][0]["slug"] == "erp"
