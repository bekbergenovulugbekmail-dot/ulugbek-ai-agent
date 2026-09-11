"""Data access for :class:`~ulugbek_ai.identity.models.User`."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.identity.models import User

#: Identifier of the implicit single owner until authentication lands.
DEFAULT_USER_EXTERNAL_ID = "default"


class UserRepository:
    """All SQL for users lives here."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_external_id(self, external_id: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.external_id == external_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        external_id: str,
        display_name: str | None = None,
        email: str | None = None,
    ) -> User:
        user = User(
            external_id=external_id, display_name=display_name, email=email
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_or_create_default(self) -> User:
        """Return the implicit owner, creating it on first use."""
        existing = await self.get_by_external_id(DEFAULT_USER_EXTERNAL_ID)
        if existing is not None:
            return existing
        return await self.create(
            external_id=DEFAULT_USER_EXTERNAL_ID, display_name="Default user"
        )

    async def list_all(self, *, limit: int = 100) -> list[User]:
        result = await self._session.execute(
            select(User).order_by(User.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
