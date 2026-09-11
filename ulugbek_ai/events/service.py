"""Event queries and agent-state derivation.

The state snapshot is what keeps the operator out of "Claude is doing
something" limbo: it names the concrete phase the agent is in, derived from the
run row and the most recent trace entry.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.models import AgentRun, AgentStep
from ulugbek_ai.agent.repository import AgentRunRepository
from ulugbek_ai.approvals.repository import ApprovalRepository
from ulugbek_ai.core.enums import RunStatus, StepType, TaskStatus
from ulugbek_ai.core.errors import NotFoundError
from ulugbek_ai.events.projector import project_step, project_steps
from ulugbek_ai.events.repository import EventRepository
from ulugbek_ai.events.schemas import AgentEvent, AgentStateSnapshot, EventPage
from ulugbek_ai.events.types import PHASE_LABELS, AgentPhase
from ulugbek_ai.tasks.repository import TaskRepository

#: Which phase the agent is in, given the last trace entry of a running run.
_PHASE_BY_STEP: dict[StepType, AgentPhase] = {
    StepType.AGENT_STARTED: AgentPhase.UNDERSTANDING,
    StepType.AGENT_RUN: AgentPhase.LOADING_CONTEXT,
    StepType.PLAN: AgentPhase.PLANNING,
    StepType.REPLAN: AgentPhase.PLANNING,
    StepType.LLM_CALL: AgentPhase.THINKING,
    StepType.TOOL_CALL: AgentPhase.USING_TOOL,
    StepType.TOOL_RESULT: AgentPhase.THINKING,
    StepType.PERMISSION: AgentPhase.USING_TOOL,
    StepType.APPROVAL: AgentPhase.WAITING_APPROVAL,
    StepType.VERIFICATION_STARTED: AgentPhase.VERIFYING,
    StepType.VERIFICATION: AgentPhase.VERIFYING,
}

_TERMINAL_PHASES: dict[RunStatus, AgentPhase] = {
    RunStatus.COMPLETED: AgentPhase.COMPLETED,
    RunStatus.FAILED: AgentPhase.FAILED,
    RunStatus.CANCELLED: AgentPhase.CANCELLED,
    RunStatus.WAITING_APPROVAL: AgentPhase.WAITING_APPROVAL,
}


class EventService:
    """Reads events and derives the agent's current state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._events = EventRepository(session)
        self._runs = AgentRunRepository(session)
        self._tasks = TaskRepository(session)
        self._approvals = ApprovalRepository(session)

    # ----------------------------------------------------------------- feeds #
    async def for_run(
        self,
        run_id: uuid.UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> EventPage:
        """Ordered events of a single run, for a live timeline."""
        run = await self._runs.get(run_id)
        if run is None:
            raise NotFoundError(
                f"Agent run {run_id} not found.", details={"run_id": str(run_id)}
            )
        steps = await self._events.list_for_run(
            run_id, after_sequence=after_sequence, limit=limit
        )
        events = project_steps(steps, project_id=run.project_id)
        return EventPage(
            events=events,
            cursor=steps[-1].sequence if steps else after_sequence or None,
            has_more=len(steps) == limit,
        )

    async def recent(
        self,
        *,
        types: Sequence[StepType] | None = None,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> EventPage:
        """The global activity feed, newest first."""
        steps = await self._events.list_recent(
            types=types,
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            limit=limit,
            offset=offset,
        )
        projects = await self._events.run_project_ids(
            [step.agent_run_id for step in steps]
        )
        events: list[AgentEvent] = []
        for step in steps:
            event = project_step(step, project_id=projects.get(step.agent_run_id))
            if event is not None:
                events.append(event)
        return EventPage(events=events, has_more=len(steps) == limit)

    # ----------------------------------------------------------------- state #
    async def state(self, run_id: uuid.UUID | None = None) -> AgentStateSnapshot:
        """The agent's current phase — the headline status a client renders."""
        run = (
            await self._runs.get(run_id)
            if run_id is not None
            else await self._events.latest_run()
        )
        if run is None:
            return AgentStateSnapshot(
                phase=AgentPhase.IDLE,
                label=PHASE_LABELS[AgentPhase.IDLE],
                busy=False,
            )

        task = await self._tasks.get(run.task_id) if run.task_id else None
        latest = await self._events.latest_step(run.id)
        phase = self._phase_for(run, latest)
        approval = (
            await self._approvals.get_pending_for_run(run.id)
            if phase is AgentPhase.WAITING_APPROVAL
            else None
        )

        return AgentStateSnapshot(
            phase=phase,
            label=PHASE_LABELS[phase],
            busy=phase
            not in {
                AgentPhase.IDLE,
                AgentPhase.COMPLETED,
                AgentPhase.FAILED,
                AgentPhase.CANCELLED,
            },
            run_id=run.id,
            run_status=RunStatus(run.status),
            task_id=task.id if task else None,
            task_status=TaskStatus(task.status) if task else None,
            goal=task.goal if task else run.input,
            project_id=run.project_id,
            iteration=run.iterations,
            detail=latest.summary if latest is not None else None,
            pending_approval_id=approval.id if approval else None,
            updated_at=latest.created_at if latest is not None else run.updated_at,
        )

    @staticmethod
    def _phase_for(run: AgentRun, latest: AgentStep | None) -> AgentPhase:
        status = RunStatus(run.status)
        terminal = _TERMINAL_PHASES.get(status)
        if terminal is not None:
            return terminal
        if latest is None:
            return AgentPhase.UNDERSTANDING
        return _PHASE_BY_STEP.get(StepType(latest.type), AgentPhase.RUNNING)
