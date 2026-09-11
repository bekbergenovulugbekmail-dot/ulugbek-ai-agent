"""Data access for agent runs and trace steps."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.models import AgentRun, AgentStep
from ulugbek_ai.core.enums import RunStatus


class AgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, run: AgentRun) -> AgentRun:
        self._session.add(run)
        return run

    async def flush(self) -> None:
        await self._session.flush()

    async def get(self, run_id: uuid.UUID) -> AgentRun | None:
        return await self._session.get(AgentRun, run_id)

    async def list(
        self,
        *,
        status: RunStatus | None = None,
        task_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AgentRun]:
        statement = select(AgentRun)
        if status is not None:
            statement = statement.where(AgentRun.status == status)
        if task_id is not None:
            statement = statement.where(AgentRun.task_id == task_id)
        statement = (
            statement.order_by(AgentRun.created_at.desc()).limit(limit).offset(offset)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_steps(
        self, run_id: uuid.UUID, *, limit: int = 500
    ) -> list[AgentStep]:
        result = await self._session.execute(
            select(AgentStep)
            .where(AgentStep.agent_run_id == run_id)
            .order_by(AgentStep.sequence.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def max_step_sequence(self, run_id: uuid.UUID) -> int:
        """Highest trace sequence so a resumed run continues the numbering."""
        result = await self._session.execute(
            select(func.max(AgentStep.sequence)).where(
                AgentStep.agent_run_id == run_id
            )
        )
        return int(result.scalar() or 0)
