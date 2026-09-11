"""Data access for memories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import MemoryType
from ulugbek_ai.memory.models import Memory


class MemoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, memory_id: uuid.UUID) -> Memory | None:
        return await self._session.get(Memory, memory_id)

    def add(self, memory: Memory) -> Memory:
        self._session.add(memory)
        return memory

    async def flush(self) -> None:
        await self._session.flush()

    async def delete(self, memory: Memory) -> None:
        await self._session.delete(memory)
        await self._session.flush()

    async def list(
        self,
        *,
        types: Sequence[MemoryType] | None = None,
        user_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        include_global: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Memory]:
        """List memories filtered by scope.

        ``include_global=True`` widens a project-scoped query to also return rows
        that belong to no project (facts that hold everywhere).
        """
        statement = select(Memory)

        if types:
            statement = statement.where(Memory.type.in_([t.value for t in types]))
        if user_id is not None:
            statement = statement.where(
                or_(Memory.user_id == user_id, Memory.user_id.is_(None))
                if include_global
                else Memory.user_id == user_id
            )
        if project_id is not None:
            statement = statement.where(
                or_(Memory.project_id == project_id, Memory.project_id.is_(None))
                if include_global
                else Memory.project_id == project_id
            )
        if task_id is not None:
            statement = statement.where(Memory.task_id == task_id)

        statement = (
            statement.order_by(
                Memory.importance.desc(), Memory.created_at.desc()
            )
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def find_candidates(
        self,
        terms: Sequence[str],
        *,
        user_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        types: Sequence[MemoryType] | None = None,
        limit: int = 200,
    ) -> list[Memory]:
        """Fetch a bounded candidate set for the search strategy to rank.

        The database narrows by scope and a cheap ``ILIKE`` term filter; precise
        ranking happens in :mod:`ulugbek_ai.memory.search`. This keeps the query
        portable across PostgreSQL and SQLite while still never loading the whole
        table.
        """
        statement = select(Memory)

        if project_id is not None:
            statement = statement.where(
                or_(Memory.project_id == project_id, Memory.project_id.is_(None))
            )
        if user_id is not None:
            statement = statement.where(
                or_(Memory.user_id == user_id, Memory.user_id.is_(None))
            )
        if types:
            statement = statement.where(Memory.type.in_([t.value for t in types]))

        clauses = [Memory.content.ilike(f"%{term}%") for term in terms if term]
        if clauses:
            statement = statement.where(or_(*clauses))

        statement = statement.order_by(
            Memory.importance.desc(), Memory.created_at.desc()
        ).limit(limit)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def count_for_scope(
        self, *, project_id: uuid.UUID | None = None
    ) -> int:
        statement = select(func.count()).select_from(Memory)
        if project_id is not None:
            statement = statement.where(Memory.project_id == project_id)
        result = await self._session.execute(statement)
        return int(result.scalar_one())
