"""The agent loop end to end, driven by a scripted model.

Each test queues the exact sequence of model replies the loop will consume:
first the plan, then the executor turns, then the verifier's verdict.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.engine import AgentEngine
from ulugbek_ai.agent.repository import AgentRunRepository
from ulugbek_ai.agent.schemas import AgentRunRequest
from ulugbek_ai.approvals.manager import ApprovalManager
from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.enums import (
    MemoryType,
    PermissionLevel,
    RunStatus,
    StepType,
    TaskStatus,
)
from ulugbek_ai.core.errors import ConflictError
from ulugbek_ai.llm.scripted import ScriptedLLMClient, text_response, tool_response
from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.schemas import ProjectCreate
from ulugbek_ai.tools.base import Tool, ToolResult
from ulugbek_ai.tools.registry import ToolRegistry
from tests.factories import plan_reply, verdict_reply


def engine(session: AsyncSession, llm, registry, settings) -> AgentEngine:
    return AgentEngine(session, llm=llm, registry=registry, settings=settings)


class DeployTool(Tool):
    """A CRITICAL tool, used to exercise the approval path."""

    name = "deploy"
    description = "Deploy a project to an environment."
    permission = PermissionLevel.CRITICAL
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"environment": {"type": "string"}},
        "required": ["environment"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context) -> ToolResult:
        return ToolResult.success(
            {"deployed_to": arguments["environment"]}, deployment_id="dep-1"
        )


# --------------------------------------------------------------------------- #
# Direct answer
# --------------------------------------------------------------------------- #
async def test_a_question_is_answered_and_verified(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    llm.queue(plan_reply(("Answer directly", None, "the user has an answer")))
    llm.queue(text_response("Uzbekistan's capital is Tashkent."))
    llm.queue(verdict_reply("SUCCESS", "the answer is correct and complete"))

    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="What is the capital of Uzbekistan?")
    )

    assert response.status is RunStatus.COMPLETED
    assert response.task_status is TaskStatus.COMPLETED
    assert "Tashkent" in response.output
    assert response.verification.status.value == "SUCCESS"
    assert response.iterations == 1


async def test_the_run_is_fully_traced(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("42"))
    llm.queue(verdict_reply())

    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="What is six times seven?")
    )

    steps = await AgentRunRepository(session).list_steps(response.run_id)
    recorded = [step.type for step in steps]

    assert StepType.PLAN in recorded
    assert StepType.LLM_CALL in recorded
    assert StepType.VERIFICATION in recorded
    assert StepType.FINAL_RESULT in recorded
    assert [step.sequence for step in steps] == sorted(step.sequence for step in steps)


# --------------------------------------------------------------------------- #
# Tool use
# --------------------------------------------------------------------------- #
async def test_a_tool_is_selected_executed_and_observed(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    llm.queue(
        plan_reply(
            ("Compute the total", "calculate", "a number"), requires_tools=True
        )
    )
    llm.queue(tool_response("calculate", {"expression": "17*3"}))
    llm.queue(text_response("The total is 51."))
    llm.queue(verdict_reply("SUCCESS", "the calculator returned 51"))

    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="What is 17 times 3?")
    )

    assert response.status is RunStatus.COMPLETED
    assert response.tools_used == ["calculate"]
    assert response.iterations == 2

    # The transcript carries a well-formed tool_result turn back to the model.
    run = await AgentRunRepository(session).get(response.run_id)
    tool_results = [
        block
        for message in run.transcript
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert len(tool_results) == 1
    assert tool_results[0]["is_error"] is False


async def test_a_failing_tool_is_reported_not_hidden(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    llm.queue(plan_reply(("Divide", "calculate", "a number"), requires_tools=True))
    llm.queue(tool_response("calculate", {"expression": "1/0"}))
    llm.queue(text_response("That division is undefined."))
    llm.queue(verdict_reply("SUCCESS", "correctly reported the error"))

    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="Divide one by zero.")
    )

    run = await AgentRunRepository(session).get(response.run_id)
    tool_results = [
        block
        for message in run.transcript
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]
    assert tool_results[0]["is_error"] is True
    assert "zero" in tool_results[0]["content"].lower()


async def test_an_unknown_tool_does_not_break_the_loop(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    llm.queue(plan_reply(("Do it", None, "done")))
    llm.queue(tool_response("no_such_tool", {}))
    llm.queue(text_response("That capability is not available yet."))
    llm.queue(verdict_reply("SUCCESS", "correctly reported the limitation"))

    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="Send a telegram message.")
    )

    assert response.status is RunStatus.COMPLETED


# --------------------------------------------------------------------------- #
# Verification and replanning
# --------------------------------------------------------------------------- #
async def test_a_rejected_answer_triggers_a_replan(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("It is probably fine."))
    llm.queue(verdict_reply("FAILURE", "the answer cites no evidence", ["a source"]))
    llm.queue(plan_reply(("Check with the calculator", "calculate", "a number")))
    llm.queue(text_response("The answer is 51, computed as 17*3."))
    llm.queue(verdict_reply("SUCCESS", "now justified"))

    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="What is 17 times 3?")
    )

    assert response.status is RunStatus.COMPLETED
    assert response.replans == 1
    assert "51" in response.output


async def test_repeated_verification_failure_fails_the_run(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    """The agent gives up honestly rather than shipping an unverified answer."""
    llm.queue(plan_reply(("Answer", None, "answered")))
    for _ in range(settings.agent_max_replans + 1):
        llm.queue(text_response("Trust me, it is done."))
        llm.queue(verdict_reply("FAILURE", "no evidence at all"))
        llm.queue(plan_reply(("Try again", None, "answered")))

    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="Deploy everything.")
    )

    assert response.status is RunStatus.FAILED
    assert response.task_status is TaskStatus.FAILED
    assert "Verification failed" in response.error


async def test_the_iteration_cap_stops_a_runaway_loop(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    """A model that only ever calls tools must still terminate."""
    llm.queue(plan_reply(("Loop", "current_time", "the time")))
    for index in range(settings.agent_max_iterations + 2):
        llm.queue(tool_response("current_time", {}, call_id=f"toolu_{index}"))

    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="Keep checking the time.")
    )

    assert response.status is RunStatus.FAILED
    assert "maximum" in response.error
    assert response.iterations == settings.agent_max_iterations


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #
async def test_a_critical_tool_pauses_the_run_for_approval(
    session: AsyncSession, llm: ScriptedLLMClient, registry: ToolRegistry, settings
) -> None:
    registry.register(DeployTool())
    llm.queue(plan_reply(("Deploy", "deploy", "it is live"), requires_tools=True))
    llm.queue(tool_response("deploy", {"environment": "production"}))

    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="Deploy to production.")
    )

    assert response.status is RunStatus.WAITING_APPROVAL
    assert response.task_status is TaskStatus.WAITING_APPROVAL
    assert response.approval is not None
    assert response.approval.tool_name == "deploy"
    assert response.approval.permission == "CRITICAL"
    assert response.output is None  # nothing was claimed


async def test_approving_resumes_the_run_and_executes_the_tool(
    session: AsyncSession, llm: ScriptedLLMClient, registry: ToolRegistry, settings
) -> None:
    registry.register(DeployTool())
    agent = engine(session, llm, registry, settings)

    llm.queue(plan_reply(("Deploy", "deploy", "it is live"), requires_tools=True))
    llm.queue(tool_response("deploy", {"environment": "production"}))
    paused = await agent.run(AgentRunRequest(message="Deploy to production."))

    await ApprovalManager(session).approve(
        paused.approval.approval_id, decided_by="ulugbek"
    )

    llm.queue(text_response("Deployed to production."))
    llm.queue(verdict_reply("SUCCESS", "the deploy tool returned dep-1"))
    resumed = await agent.resume(paused.run_id)

    assert resumed.status is RunStatus.COMPLETED
    assert resumed.tools_used == ["deploy"]
    assert "Deployed" in resumed.output


async def test_rejecting_lets_the_agent_continue_without_the_action(
    session: AsyncSession, llm: ScriptedLLMClient, registry: ToolRegistry, settings
) -> None:
    registry.register(DeployTool())
    agent = engine(session, llm, registry, settings)

    llm.queue(plan_reply(("Deploy", "deploy", "it is live"), requires_tools=True))
    llm.queue(tool_response("deploy", {"environment": "production"}))
    paused = await agent.run(AgentRunRequest(message="Deploy to production."))

    await ApprovalManager(session).reject(paused.approval.approval_id)

    llm.queue(text_response("I did not deploy: you rejected the action."))
    llm.queue(verdict_reply("SUCCESS", "correctly reported the rejection"))
    resumed = await agent.resume(paused.run_id)

    assert resumed.status is RunStatus.COMPLETED
    assert "did not deploy" in resumed.output

    run = await AgentRunRepository(session).get(resumed.run_id)
    rejected_result = [
        block
        for message in run.transcript
        for block in message["content"]
        if block.get("type") == "tool_result" and block.get("is_error")
    ]
    assert rejected_result
    assert "rejected" in rejected_result[0]["content"]


async def test_a_run_cannot_be_resumed_while_the_approval_is_undecided(
    session: AsyncSession, llm: ScriptedLLMClient, registry: ToolRegistry, settings
) -> None:
    registry.register(DeployTool())
    agent = engine(session, llm, registry, settings)

    llm.queue(plan_reply(("Deploy", "deploy", "it is live"), requires_tools=True))
    llm.queue(tool_response("deploy", {"environment": "production"}))
    paused = await agent.run(AgentRunRequest(message="Deploy to production."))

    with pytest.raises(ConflictError, match="undecided"):
        await agent.resume(paused.run_id)


async def test_resuming_a_completed_run_is_rejected(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings
) -> None:
    agent = engine(session, llm, registry, settings)
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("done"))
    llm.queue(verdict_reply())
    response = await agent.run(AgentRunRequest(message="hi"))

    with pytest.raises(ConflictError, match="not waiting for approval"):
        await agent.resume(response.run_id)


async def test_work_done_before_the_pause_is_not_repeated(
    session: AsyncSession, llm: ScriptedLLMClient, registry: ToolRegistry, settings
) -> None:
    """A turn mixing an allowed and a gated call must not re-run the allowed one."""
    registry.register(DeployTool())
    agent = engine(session, llm, registry, settings)

    from ulugbek_ai.llm.base import LLMResponse, LLMToolCall

    mixed = LLMResponse(
        text="",
        tool_calls=[
            LLMToolCall("toolu_a", "memory_write", {
                "content": "Production deploys need approval.",
                "type": MemoryType.DECISION.value,
            }),
            LLMToolCall("toolu_b", "deploy", {"environment": "production"}),
        ],
        content=[
            {"type": "tool_use", "id": "toolu_a", "name": "memory_write", "input": {
                "content": "Production deploys need approval.",
                "type": MemoryType.DECISION.value,
            }},
            {"type": "tool_use", "id": "toolu_b", "name": "deploy",
             "input": {"environment": "production"}},
        ],
        stop_reason="tool_use",
    )

    llm.queue(plan_reply(("Note then deploy", "deploy", "it is live")))
    llm.queue(mixed)
    paused = await agent.run(AgentRunRequest(message="Note it, then deploy."))
    assert paused.status is RunStatus.WAITING_APPROVAL

    from ulugbek_ai.memory.manager import MemoryManager

    memories_before = len(await MemoryManager(session).list_memories())

    await ApprovalManager(session).approve(paused.approval.approval_id)
    llm.queue(text_response("Noted and deployed."))
    llm.queue(verdict_reply())
    resumed = await agent.resume(paused.run_id)

    assert resumed.status is RunStatus.COMPLETED
    # The memory write ran exactly once, despite the pause in the same turn.
    assert len(await MemoryManager(session).list_memories()) == memories_before + 1


# --------------------------------------------------------------------------- #
# Context, routing and failure handling
# --------------------------------------------------------------------------- #
async def test_the_run_is_routed_to_a_project(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    project = await ProjectManager(session).create(
        ProjectCreate(name="ERP", keywords=["invoice"])
    )
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("Invoice numbering starts at 1000."))
    llm.queue(verdict_reply())

    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="How does invoice numbering work in ERP?")
    )

    assert response.project_id == project.id


async def test_only_relevant_memory_reaches_the_prompt(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    """The whole memory table must never be dumped into the context."""
    from ulugbek_ai.memory.manager import MemoryManager

    memories = MemoryManager(session)
    await memories.remember("Deploys always go to staging first.", type=MemoryType.DECISION)
    for index in range(30):
        await memories.remember(f"Unrelated trivia number {index}.", type=MemoryType.FACT)

    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("Staging first."))
    llm.queue(verdict_reply())

    await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="Where do deploys go first?")
    )

    planner_prompt = llm.calls[0].messages[0].text
    assert "Deploys always go to staging first." in planner_prompt
    assert "Unrelated trivia number 17." not in planner_prompt
    assert len(planner_prompt) < settings.memory_context_max_chars * 2


async def test_an_llm_failure_fails_the_run_cleanly(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="anything")  # no replies queued -> LLMError
    )

    assert response.status is RunStatus.FAILED
    assert response.error


async def test_the_outcome_is_remembered_for_next_time(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings: Settings
) -> None:
    from ulugbek_ai.memory.manager import MemoryManager

    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("Tashkent."))
    llm.queue(verdict_reply())

    await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="What is the capital of Uzbekistan?")
    )

    stored = await MemoryManager(session).list_memories(types=[MemoryType.TASK_CONTEXT])
    assert len(stored) == 1
    assert "Tashkent" in stored[0].content
