"""Built-in tools, including the safety properties of the calculator."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import MemoryType, PermissionLevel, VerificationStatus
from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.schemas import ProjectCreate
from ulugbek_ai.tools.base import ToolContext
from ulugbek_ai.tools.builtin import (
    CalculatorTool,
    ClockTool,
    MemorySearchTool,
    MemoryWriteTool,
    ProjectCreateTool,
    ProjectListTool,
    default_tools,
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


# --------------------------------------------------------------------------- #
# Creating a project
#
# Until this existed the only way to register a project was an HTTP call the
# operator had to make themselves, which left the agent unable to act on
# anything: with no project there is no repository, and the GitHub tools refuse
# without one.
# --------------------------------------------------------------------------- #
async def test_project_create_registers_a_project(session: AsyncSession) -> None:
    result = await ProjectCreateTool().execute(
        {"name": "Ulugbek AI Agent", "repository": "acme/widgets"},
        ToolContext(session=session),
    )

    assert result.ok
    assert result.output["slug"] == "ulugbek-ai-agent"
    assert result.output["repository"] == "acme/widgets"

    listed = await ProjectListTool().execute({}, ToolContext(session=session))
    assert listed.output["count"] == 1
    assert listed.output["projects"][0]["repository"] == "acme/widgets"


async def test_project_create_is_what_unblocks_the_github_tools(
    session: AsyncSession,
) -> None:
    """The repository is the point, so it is stored rather than quietly dropped."""
    await ProjectCreateTool().execute(
        {"name": "Warehouse", "repository": "acme/warehouse"},
        ToolContext(session=session),
    )

    project = (await ProjectManager(session).list())[0]
    assert project.repository == "acme/warehouse"


async def test_project_create_needs_a_session() -> None:
    result = await ProjectCreateTool().execute({"name": "X"}, ToolContext())
    assert result.ok is False
    assert "session" in result.error


async def test_a_repository_that_is_not_owner_slash_name_is_refused(
    session: AsyncSession,
) -> None:
    """Caught here, where the mistake was made.

    A project carrying `github.com/acme/widgets` is accepted by the database
    and then fails at every GitHub call afterwards, far from the typo.
    """
    for bad in ("widgets", "https://github.com/acme/widgets", "acme/widgets/extra"):
        result = await ProjectCreateTool().execute(
            {"name": "X", "repository": bad}, ToolContext(session=session)
        )
        assert result.ok is False, bad
        assert "owner/name" in result.error


async def test_a_second_project_with_the_same_name_fails_cleanly(
    session: AsyncSession,
) -> None:
    arguments = {"name": "ERP"}
    first = await ProjectCreateTool().execute(arguments, ToolContext(session=session))
    assert first.ok

    second = await ProjectCreateTool().execute(arguments, ToolContext(session=session))
    assert second.ok is False
    assert "erp" in second.error
    # An explanation, not a traceback: the model reads this and has to be able
    # to act on it.
    assert "already exists" in second.error


async def test_project_create_reads_the_row_back(session: AsyncSession) -> None:
    context = ToolContext(session=session)
    arguments = {"name": "Verified"}
    result = await ProjectCreateTool().execute(arguments, context)

    verification = await ProjectCreateTool().verify(arguments, result, context)
    assert verification.status is VerificationStatus.SUCCESS


async def test_a_failed_creation_does_not_verify(session: AsyncSession) -> None:
    context = ToolContext(session=session)
    arguments = {"name": "X", "repository": "no-owner"}
    result = await ProjectCreateTool().execute(arguments, context)

    verification = await ProjectCreateTool().verify(arguments, result, context)
    assert verification.status is VerificationStatus.FAILURE


def test_creating_a_project_is_a_write_and_is_not_retried() -> None:
    """The risk contract, stated rather than inherited by accident."""
    tool = ProjectCreateTool()
    assert tool.permission is PermissionLevel.WRITE
    assert tool.idempotent is False


def test_the_agent_actually_gets_this_tool() -> None:
    """A tool nobody registered is a class.

    The registry builds from `default_tools()`, so a tool that is written,
    tested and never added there passes every test above and does not exist
    as far as the model is concerned.
    """
    assert "project_create" in {tool.name for tool in default_tools()}
