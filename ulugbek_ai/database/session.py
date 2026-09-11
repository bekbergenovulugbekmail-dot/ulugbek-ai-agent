"""Async engine and session lifecycle.

The :class:`Database` object owns exactly one engine and one sessionmaker. It is
constructed from :class:`~ulugbek_ai.config.settings.Settings`, so tests can spin
up an independent instance (e.g. SQLite) without touching global state.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from ulugbek_ai.config.settings import Settings, get_settings
from ulugbek_ai.database.base import Base
from ulugbek_ai.database.registry import import_all_models


class Database:
    """Owns the async engine and hands out sessions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_pre_ping=True,
            future=True,
            **self._pool_options(settings),
        )
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    @staticmethod
    def _pool_options(settings: Settings) -> dict[str, Any]:
        """SQLite has no connection pool sizing; Postgres does."""
        if settings.database_url.startswith("sqlite"):
            return {}
        return {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
        }

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Transactional scope: commit on success, roll back on failure."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def create_all(self) -> None:
        """Create every table. Migrations own this in production; tests use it."""
        import_all_models()
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def drop_all(self) -> None:
        import_all_models()
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)

    async def dispose(self) -> None:
        await self._engine.dispose()


_database: Database | None = None


def get_database(settings: Settings | None = None) -> Database:
    """Process-wide :class:`Database` singleton."""
    global _database
    if _database is None:
        _database = Database(settings or get_settings())
    return _database


async def reset_database() -> None:
    """Dispose of the singleton (used on application shutdown and in tests)."""
    global _database
    if _database is not None:
        await _database.dispose()
        _database = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""
    async with get_database().session() as session:
        yield session
