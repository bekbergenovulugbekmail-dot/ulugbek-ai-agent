"""FastAPI dependencies.

:func:`require_principal` is the authentication seam. It is not listed on
individual routes — ``ulugbek_ai/api/router.py`` applies it to whole routers, so
a new endpoint is protected by being added rather than by being remembered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.engine import AgentEngine
from ulugbek_ai.agent.runner import BackgroundAgentRunner
from ulugbek_ai.api.auth import (
    OPERATOR,
    Principal,
    bearer_from_header,
    configured_token_problem,
    token_matches,
)
from ulugbek_ai.config.settings import Settings, get_settings
from ulugbek_ai.llm.claude import workspace_id_problem
from ulugbek_ai.core.errors import AuthenticationError, ConfigurationError
from ulugbek_ai.database.session import Database, get_database
from ulugbek_ai.events.service import EventService
from ulugbek_ai.llm.base import LLMClient
from ulugbek_ai.tools.registry import ToolRegistry


async def session_dependency() -> AsyncIterator[AsyncSession]:
    """A transactional database session, committed when the request succeeds."""
    async with get_database().session() as session:
        yield session


def settings_dependency(request: Request) -> Settings:
    """The settings this application was built with.

    ``create_app`` takes a settings object and stores it on the app, so reading
    the process-wide singleton here would let an endpoint report a
    configuration the application is not actually running — health being the
    one that matters, since it is what a deployment is judged by. The singleton
    remains the fallback for a request served outside a configured app.
    """
    configured: Settings | None = getattr(request.app.state, "settings", None)
    return configured or get_settings()


def llm_dependency(request: Request) -> LLMClient:
    """The shared LLM client created during application startup."""
    client: LLMClient | None = getattr(request.app.state, "llm", None)
    if client is None:
        # Say which piece of configuration is missing. "Set ANTHROPIC_API_KEY"
        # is wrong and costly advice when the key is fine and the workspace id
        # is the problem.
        settings: Settings | None = getattr(request.app.state, "settings", None)
        problem = (
            workspace_id_problem(settings.anthropic_workspace_id)
            if settings is not None
            else None
        )
        if problem is not None:
            raise ConfigurationError(
                f"ANTHROPIC_WORKSPACE_ID is not usable: {problem}. Correct it "
                "and restart the application."
            )
        raise ConfigurationError(
            "The LLM client is not configured. Set ANTHROPIC_API_KEY and "
            "restart the application."
        )
    return client


def registry_dependency(request: Request) -> ToolRegistry:
    """The shared tool registry created during application startup."""
    registry: ToolRegistry | None = getattr(request.app.state, "registry", None)
    if registry is None:  # pragma: no cover - set unconditionally on startup
        raise ConfigurationError("The tool registry is not configured.")
    return registry


def database_dependency() -> Database:
    """The shared :class:`Database`.

    Streaming endpoints need to open their own short-lived sessions rather than
    hold the request-scoped one open for the life of the connection.
    """
    return get_database()


def optional_runner_dependency(
    request: Request,
) -> BackgroundAgentRunner | None:
    """The background runner, or ``None`` when the agent is not configured.

    Routes that only *optionally* run work in the background depend on this, so
    a missing API key does not break their synchronous path.
    """
    return getattr(request.app.state, "runner", None)


def runner_dependency(request: Request) -> BackgroundAgentRunner:
    """The background runner; required, so a missing one is an error."""
    runner = optional_runner_dependency(request)
    if runner is None:
        raise ConfigurationError(
            "The agent runner is not configured. Set ANTHROPIC_API_KEY and "
            "restart the application."
        )
    return runner


def require_principal(request: Request) -> Principal:
    """Prove the caller is the operator, or refuse the request.

    Two different failures, kept apart because they need different fixes: a
    server with no token configured is broken (503, and it says which variable
    is missing), while a caller without a valid one is unauthenticated (401,
    with the challenge header that makes it actionable).
    """
    settings = settings_dependency(request)
    secret = settings.auth_token

    problem = configured_token_problem(
        secret.get_secret_value() if secret else None
    )
    if problem is not None:
        raise ConfigurationError(
            f"AUTH_TOKEN is not usable: {problem}. The API serves nothing but "
            "health until it is set to a long random string."
        )

    presented = bearer_from_header(request.headers.get("Authorization"))
    if presented is None or not token_matches(
        presented, secret.get_secret_value()  # type: ignore[union-attr]
    ):
        # The same answer either way: saying which half was wrong tells an
        # attacker whether a token is worth refining.
        raise AuthenticationError(
            "This endpoint requires the operator token. Send it in the "
            "Authorization header."
        )

    return OPERATOR


SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
DatabaseDep = Annotated[Database, Depends(database_dependency)]
RunnerDep = Annotated[BackgroundAgentRunner, Depends(runner_dependency)]
OptionalRunnerDep = Annotated[
    BackgroundAgentRunner | None, Depends(optional_runner_dependency)
]
SettingsDep = Annotated[Settings, Depends(settings_dependency)]
LLMDep = Annotated[LLMClient, Depends(llm_dependency)]
RegistryDep = Annotated[ToolRegistry, Depends(registry_dependency)]
PrincipalDep = Annotated[Principal, Depends(require_principal)]


def engine_dependency(
    session: SessionDep,
    llm: LLMDep,
    registry: RegistryDep,
    settings: SettingsDep,
) -> AgentEngine:
    """An :class:`AgentEngine` bound to this request's session."""
    return AgentEngine(session, llm=llm, registry=registry, settings=settings)


EngineDep = Annotated[AgentEngine, Depends(engine_dependency)]


def event_service_dependency(session: SessionDep) -> EventService:
    """Event queries bound to this request's session."""
    return EventService(session)


EventServiceDep = Annotated[EventService, Depends(event_service_dependency)]
