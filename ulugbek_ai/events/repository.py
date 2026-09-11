"""Queries backing the event stream and the dashboard counters."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.models import AgentRun, AgentStep
from ulugbek_ai.core.enums import StepType


class EventRepository:
    """Reads the audit trail as an ordered event source."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_run(
        self,
        run_id: uuid.UUID,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> list[AgentStep]:
        """Events of one run, oldest first — the shape a live timeline wants."""
        result = await self._session.execute(
            select(AgentStep)
            .where(
                AgentStep.agent_run_id == run_id,
                AgentStep.sequence > after_sequence,
            )
            .order_by(AgentStep.sequence.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_recent(
        self,
        *,
        types: Sequence[StepType] | None = None,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AgentStep]:
        """The global feed, newest first."""
        statement = select(AgentStep)
        if project_id is not None:
            statement = statement.join(
                AgentRun, AgentRun.id == AgentStep.agent_run_id
            ).where(AgentRun.project_id == project_id)
        if run_id is not None:
            statement = statement.where(AgentStep.agent_run_id == run_id)
        if task_id is not None:
            statement = statement.where(AgentStep.task_id == task_id)
        if types:
            statement = statement.where(
                AgentStep.type.in_([step_type.value for step_type in types])
            )
        statement = (
            statement.order_by(
                AgentStep.created_at.desc(), AgentStep.sequence.desc()
            )
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def run_project_ids(
        self, run_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, uuid.UUID | None]:
        """Map run -> project, so events can be tagged without an N+1 query."""
        if not run_ids:
            return {}
        result = await self._session.execute(
            select(AgentRun.id, AgentRun.project_id).where(
                AgentRun.id.in_(list(run_ids))
            )
        )
        return {row[0]: row[1] for row in result.all()}

    async def latest_run(self) -> AgentRun | None:
        result = await self._session.execute(
            select(AgentRun).order_by(AgentRun.created_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_step(self, run_id: uuid.UUID) -> AgentStep | None:
        result = await self._session.execute(
            select(AgentStep)
            .where(AgentStep.agent_run_id == run_id)
            .order_by(AgentStep.sequence.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_steps(self, run_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(AgentStep)
            .where(AgentStep.agent_run_id == run_id)
        )
        return int(result.scalar_one())
