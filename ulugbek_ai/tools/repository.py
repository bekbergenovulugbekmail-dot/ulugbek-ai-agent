"""Data access for tool execution records."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import ToolExecutionStatus
from ulugbek_ai.tools.models import ToolExecution


class ToolExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, execution: ToolExecution) -> ToolExecution:
        self._session.add(execution)
        return execution

    async def flush(self) -> None:
        await self._session.flush()

    async def get(self, execution_id: uuid.UUID) -> ToolExecution | None:
        return await self._session.get(ToolExecution, execution_id)

    async def list(
        self,
        *,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        tool_name: str | None = None,
        status: ToolExecutionStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ToolExecution]:
        """Execution history, newest first, filtered on the usual axes."""
        statement = select(ToolExecution)
        if run_id is not None:
            statement = statement.where(ToolExecution.agent_run_id == run_id)
        if task_id is not None:
            statement = statement.where(ToolExecution.task_id == task_id)
        if tool_name is not None:
            statement = statement.where(ToolExecution.tool_name == tool_name)
        if status is not None:
            statement = statement.where(ToolExecution.status == status)
        statement = (
            statement.order_by(ToolExecution.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_for_run(
        self, run_id: uuid.UUID, *, limit: int = 100
    ) -> list[ToolExecution]:
        result = await self._session.execute(
            select(ToolExecution)
            .where(ToolExecution.agent_run_id == run_id)
            .order_by(ToolExecution.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())
