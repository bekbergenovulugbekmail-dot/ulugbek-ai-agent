"""The event layer: projection, feeds, live state and the background runner."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.engine import AgentEngine
from ulugbek_ai.agent.runner import BackgroundAgentRunner
from ulugbek_ai.agent.schemas import AgentRunRequest
from ulugbek_ai.core.enums import PermissionLevel, RunStatus, StepType
from ulugbek_ai.events.projector import project_step, project_steps
from ulugbek_ai.events.service import EventService
from ulugbek_ai.events.types import AgentPhase, EventStatus, EventType
from ulugbek_ai.llm.scripted import ScriptedLLMClient, text_response, tool_response
from ulugbek_ai.tools.base import Tool, ToolResult
from tests.factories import plan_reply, verdict_reply


class DeployTool(Tool):
    name = "deploy"
    description = "Deploy a project to an environment."
    permission = PermissionLevel.CRITICAL

    async def execute(self, arguments, context) -> ToolResult:
        return ToolResult.success({"ok": True})


def engine(session, llm, registry, settings) -> AgentEngine:
    return AgentEngine(session, llm=llm, registry=registry, settings=settings)


# --------------------------------------------------------------------------- #
# Projection
# --------------------------------------------------------------------------- #
class _Step:
    """A minimal stand-in for an AgentStep row."""

    def __init__(self, step_type, summary="", payload=None, success=None):
        import uuid

        from ulugbek_ai.core.utils import utcnow

        self.id = uuid.uuid4()
        self.agent_run_id = uuid.uuid4()
        self.task_id = None
        self.sequence = 1
        self.iteration = 1
        self.type = step_type
        self.summary = summary
        self.payload = payload or {}
        self.success = success
        self.created_at = utcnow()


def test_tool_result_projects_by_outcome() -> None:
    ok = project_step(_Step(StepType.TOOL_RESULT, "Tool deploy: ok", success=True))
    bad = project_step(_Step(StepType.TOOL_RESULT, "Tool deploy: failed", success=False))

    assert ok.type is EventType.TOOL_COMPLETED
    assert ok.status is EventStatus.SUCCESS
    assert bad.type is EventType.TOOL_FAILED
    assert bad.status is EventStatus.FAILURE


def test_approval_projects_as_waiting() -> None:
    event = project_step(
        _Step(StepType.APPROVAL, "Approval required", {"tool": "deploy"})
    )

    assert event.type is EventType.APPROVAL_REQUIRED
    assert event.status is EventStatus.WAITING
    assert event.subject == "deploy"


def test_projection_summarizes_rather_than_forwarding_the_whole_payload() -> None:
    """The stream carries what a UI renders, not the entire audit row."""
    event = project_step(
        _Step(
            StepType.TOOL_RESULT,
            "Tool deploy: ok",
            {
                "tool": "deploy",
                "result": {
                    "ok": True,
                    "duration_ms": 42,
                    "output": {"enormous": "payload"},
                },
            },
            success=True,
        )
    )

    assert event.metadata["ok"] is True
    assert event.metadata["duration_ms"] == 42
    assert "output" not in event.metadata


def test_unrenderable_steps_are_dropped() -> None:
    assert project_step(_Step(StepType.TASK, "internal")) is None
    assert project_steps([_Step(StepType.TASK, "internal")]) == []


# --------------------------------------------------------------------------- #
# Feeds and state
# --------------------------------------------------------------------------- #
async def test_a_run_produces_a_readable_timeline(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings
) -> None:
    llm.queue(plan_reply(("Compute", "calculate", "a number"), requires_tools=True))
    llm.queue(tool_response("calculate", {"expression": "2+2"}))
    llm.queue(text_response("The answer is 4."))
    llm.queue(verdict_reply("SUCCESS", "the calculator returned 4"))

    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="What is 2+2?")
    )

    page = await EventService(session).for_run(response.run_id)
    types = [event.type for event in page.events]

    assert EventType.AGENT_STARTED in types
    assert EventType.AGENT_PLANNING in types
    assert EventType.TOOL_STARTED in types
    assert EventType.TOOL_COMPLETED in types
    assert EventType.VERIFICATION_STARTED in types
    assert EventType.TASK_COMPLETED in types
    assert types.index(EventType.TOOL_STARTED) < types.index(EventType.TOOL_COMPLETED)
    assert all(event.safe_message for event in page.events)


async def test_the_cursor_returns_only_new_events(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings
) -> None:
    """This is what makes polling and SSE reconnection cheap."""
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("done"))
    llm.queue(verdict_reply())
    response = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="hi")
    )

    service = EventService(session)
    first = await service.for_run(response.run_id)
    again = await service.for_run(response.run_id, after_sequence=first.cursor)

    assert first.events
    assert again.events == []


async def test_idle_state_when_nothing_has_run(session: AsyncSession) -> None:
    state = await EventService(session).state()

    assert state.phase is AgentPhase.IDLE
    assert state.busy is False
    assert state.run_id is None


async def test_state_after_a_completed_run(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings
) -> None:
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("done"))
    llm.queue(verdict_reply())
    await engine(session, llm, registry, settings).run(AgentRunRequest(message="hi"))

    state = await EventService(session).state()

    assert state.phase is AgentPhase.COMPLETED
    assert state.busy is False
    assert state.label == "Completed"


async def test_state_reports_waiting_approval_with_the_approval_id(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings
) -> None:
    registry.register(DeployTool())
    llm.queue(plan_reply(("Deploy", "deploy", "live"), requires_tools=True))
    llm.queue(tool_response("deploy", {}))

    paused = await engine(session, llm, registry, settings).run(
        AgentRunRequest(message="Deploy it.")
    )

    state = await EventService(session).state()

    assert state.phase is AgentPhase.WAITING_APPROVAL
    assert state.busy is True
    assert state.pending_approval_id == paused.approval.approval_id


async def test_the_global_feed_is_newest_first(
    session: AsyncSession, llm: ScriptedLLMClient, registry, settings
) -> None:
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("done"))
    llm.queue(verdict_reply())
    await engine(session, llm, registry, settings).run(AgentRunRequest(message="hi"))

    page = await EventService(session).recent(limit=10)
    timestamps = [event.timestamp for event in page.events]

    assert page.events
    assert timestamps == sorted(timestamps, reverse=True)


# --------------------------------------------------------------------------- #
# Background runner
# --------------------------------------------------------------------------- #
async def test_the_runner_executes_a_started_run(
    database, session: AsyncSession, llm: ScriptedLLMClient, registry, settings
) -> None:
    """start() returns immediately; the runner finishes the work."""
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("Tashkent."))
    llm.queue(verdict_reply())

    run, _ = await engine(session, llm, registry, settings).start(
        AgentRunRequest(message="Capital of Uzbekistan?")
    )
    await session.commit()
    assert run.status is RunStatus.RUNNING

    runner = BackgroundAgentRunner(
        database, llm=llm, registry=registry, settings=settings
    )
    runner.launch(run.id)
    for _ in range(100):
        if runner.active_count == 0:
            break
        await asyncio.sleep(0.02)

    async with database.session() as check:
        from ulugbek_ai.agent.repository import AgentRunRepository

        finished = await AgentRunRepository(check).get(run.id)
        assert finished.status is RunStatus.COMPLETED
        assert finished.output == "Tashkent."


async def test_a_crashing_run_is_marked_failed_not_left_running(
    database, session: AsyncSession, registry, settings
) -> None:
    """A run must never sit in RUNNING forever because of an exception."""
    llm = ScriptedLLMClient()  # no queued replies -> the planner raises
    run, _ = await engine(session, llm, registry, settings).start(
        AgentRunRequest(message="anything")
    )
    await session.commit()

    runner = BackgroundAgentRunner(
        database, llm=llm, registry=registry, settings=settings
    )
    runner.launch(run.id)
    for _ in range(100):
        if runner.active_count == 0:
            break
        await asyncio.sleep(0.02)

    async with database.session() as check:
        from ulugbek_ai.agent.repository import AgentRunRepository

        finished = await AgentRunRepository(check).get(run.id)
        assert finished.status is RunStatus.FAILED
        assert finished.error


async def test_events_are_visible_while_the_run_is_still_going(
    database, session: AsyncSession, registry, settings
) -> None:
    """The guarantee the live stream depends on.

    A run holds one session for its whole life. If the audit trail were only
    committed at the end, an observer in another session would see nothing
    until the run finished — the "live" timeline would be a replay. This test
    reads the trail from a *separate* session while the run is mid-flight.
    """
    import json

    from ulugbek_ai.llm.base import LLMClient, LLMResponse, LLMUsage

    observed: list[int] = []

    class ObservingClient(LLMClient):
        """Counts committed events from another session on every model call."""

        model = "observing"

        def __init__(self, replies: list[LLMResponse]) -> None:
            self._replies = replies

        async def complete(self, messages, **kwargs):
            async with database.session() as watcher:
                page = await EventService(watcher).recent(limit=50)
                observed.append(len(page.events))
            return self._replies.pop(0)

    def reply(text: str) -> LLMResponse:
        return LLMResponse(
            text=text,
            content=[{"type": "text", "text": text}],
            stop_reason="end_turn",
            usage=LLMUsage(1, 1),
        )

    plan_json = json.dumps(
        {
            "goal_restatement": "Answer",
            "requires_tools": False,
            "reasoning": "direct",
            "steps": [
                {"description": "Answer", "tool": None, "expected_outcome": "answered"}
            ],
        }
    )
    verdict_json = json.dumps(
        {"status": "SUCCESS", "reason": "fine", "missing": []}
    )

    llm = ObservingClient([reply(plan_json), reply("done"), reply(verdict_json)])
    await AgentEngine(
        session, llm=llm, registry=registry, settings=settings
    ).run(AgentRunRequest(message="hi"))

    # The planner call already sees committed events, and the count keeps
    # growing across the run instead of appearing all at once at the end.
    assert observed[0] >= 1
    assert observed[-1] > observed[0]
