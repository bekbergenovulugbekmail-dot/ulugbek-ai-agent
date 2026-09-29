"""Shared test fixtures.

Tests run against a real database. ``TEST_DATABASE_URL`` points at PostgreSQL —
the production engine — and falls back to in-memory SQLite so the suite still
runs on a machine without a server. No test ever reaches the network: the LLM is
always the scripted double from :mod:`ulugbek_ai.llm.scripted`.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.runner import BackgroundAgentRunner
from ulugbek_ai.config.settings import Settings
from ulugbek_ai.database.base import Base
from ulugbek_ai.database.registry import import_all_models
from ulugbek_ai.database.session import Database
from ulugbek_ai.identity.repository import UserRepository
from ulugbek_ai.llm.scripted import ScriptedLLMClient
from ulugbek_ai.tools.permissions import PermissionPolicy, PermissionService
from ulugbek_ai.tools.registry import build_default_registry

DEFAULT_TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


def test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


#: The operator token the suite authenticates with. Not a credential: it
#: protects nothing, exists only in this file, and is deliberately shaped so
#: that a real token could never be mistaken for it.
OPERATOR_TOKEN = "test-operator-token-not-a-real-credential"


@pytest.fixture
def settings() -> Settings:
    """Settings isolated from any ``.env`` on the machine."""
    return Settings(
        _env_file=None,
        environment="test",
        auth_token=OPERATOR_TOKEN,
        database_url=test_database_url(),
        agent_max_iterations=5,
        agent_max_replans=1,
        memory_context_limit=10,
        memory_context_max_chars=4_000,
    )


@pytest_asyncio.fixture
async def database(settings: Settings) -> AsyncIterator[Database]:
    """A database with the full schema created and dropped per test."""
    import_all_models()
    db = Database(settings)
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield db
    finally:
        async with db.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await db.dispose()


@pytest_asyncio.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    """A session that commits, so repositories behave as they do in production."""
    async with database.session() as session:
        yield session


@pytest.fixture
def llm() -> ScriptedLLMClient:
    """A scripted LLM client; queue responses per test."""
    return ScriptedLLMClient()


@pytest.fixture
def registry(settings: Settings):
    """Registry with the built-in tools under the default policy."""
    return build_default_registry(
        permissions=PermissionService(PermissionPolicy.from_settings(settings))
    )


@pytest_asyncio.fixture
async def default_user(session: AsyncSession):
    return await UserRepository(session).get_or_create_default()


@pytest_asyncio.fixture
async def client(
    database: Database, settings: Settings, llm: ScriptedLLMClient, registry
) -> AsyncIterator[AsyncClient]:
    """HTTP client wired to the app with test doubles injected.

    Overriding the session dependency (rather than the global singleton) keeps
    each test on its own engine.
    """
    from ulugbek_ai.api.deps import database_dependency, session_dependency
    from ulugbek_ai.main import create_app

    app = create_app(settings)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with database.session() as session:
            yield session

    app.dependency_overrides[session_dependency] = override_session
    app.dependency_overrides[database_dependency] = lambda: database
    app.state.llm = llm
    app.state.registry = registry
    app.state.runner = BackgroundAgentRunner(
        database, llm=llm, registry=registry, settings=settings
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"},
    ) as http:
        yield http


@pytest_asyncio.fixture
async def anonymous_client(
    database: Database, settings: Settings, llm: ScriptedLLMClient, registry
) -> AsyncIterator[AsyncClient]:
    """The same app, reached without a credential.

    A separate fixture rather than a flag: a test that forgets to authenticate
    should fail loudly, not quietly exercise the unauthenticated path.
    """
    from ulugbek_ai.api.deps import database_dependency, session_dependency
    from ulugbek_ai.main import create_app

    app = create_app(settings)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with database.session() as session:
            yield session

    app.dependency_overrides[session_dependency] = override_session
    app.dependency_overrides[database_dependency] = lambda: database
    app.state.llm = llm
    app.state.registry = registry
    app.state.runner = BackgroundAgentRunner(
        database, llm=llm, registry=registry, settings=settings
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http
