"""Data access for tasks."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import TaskStatus
from ulugbek_ai.tasks.models import Task


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, task_id: uuid.UUID) -> Task | None:
        return await self._session.get(Task, task_id)

    def add(self, task: Task) -> Task:
        self._session.add(task)
        return task

    async def flush(self) -> None:
        await self._session.flush()

    async def list(
        self,
        *,
        status: TaskStatus | None = None,
        project_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Task]:
        statement = select(Task)
        if status is not None:
            statement = statement.where(Task.status == status)
        if project_id is not None:
            statement = statement.where(Task.project_id == project_id)
        if user_id is not None:
            statement = statement.where(Task.user_id == user_id)
        statement = (
            statement.order_by(Task.created_at.desc()).limit(limit).offset(offset)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_recent_for_project(
        self, project_id: uuid.UUID, *, limit: int = 5
    ) -> list[Task]:
        return await self.list(project_id=project_id, limit=limit)
