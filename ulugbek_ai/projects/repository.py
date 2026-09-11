"""Data access for projects."""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import ProjectStatus
from ulugbek_ai.projects.models import Project


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def get_by_slug(self, slug: str) -> Project | None:
        result = await self._session.execute(
            select(Project).where(Project.slug == slug)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        status: ProjectStatus | None = None,
        owner_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Project]:
        statement = select(Project)
        if status is not None:
            statement = statement.where(Project.status == status)
        if owner_id is not None:
            statement = statement.where(Project.owner_id == owner_id)
        statement = (
            statement.order_by(Project.created_at.desc()).limit(limit).offset(offset)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_active(self, *, limit: int = 200) -> list[Project]:
        return await self.list(status=ProjectStatus.ACTIVE, limit=limit)

    async def search_by_text(self, text: str, *, limit: int = 20) -> list[Project]:
        """Case-insensitive name/description match — used by project routing."""
        pattern = f"%{text.lower()}%"
        result = await self._session.execute(
            select(Project)
            .where(
                or_(
                    Project.name.ilike(pattern),
                    Project.slug.ilike(pattern),
                    Project.description.ilike(pattern),
                )
            )
            .limit(limit)
        )
        return list(result.scalars().all())

    def add(self, project: Project) -> Project:
        self._session.add(project)
        return project

    async def flush(self) -> None:
        await self._session.flush()

    async def delete(self, project: Project) -> None:
        await self._session.delete(project)
        await self._session.flush()
