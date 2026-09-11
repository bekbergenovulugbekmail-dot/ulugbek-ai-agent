"""FastAPI dependencies.

Authentication is deliberately a seam rather than an implementation: every
protected route depends on :func:`require_principal`, so adding real auth in a
later phase means replacing one function, not touching the routes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.engine import AgentEngine
from ulugbek_ai.config.settings import Settings, get_settings
from ulugbek_ai.core.errors import ConfigurationError
from ulugbek_ai.database.session import get_database
from ulugbek_ai.llm.base import LLMClient
from ulugbek_ai.tools.registry import ToolRegistry


@dataclass(slots=True)
class Principal:
    """Who is making the request.

    Until authentication lands there is exactly one principal, the local
    operator. The type exists so routes can already depend on it.
    """

    subject: str = "local-operator"
    is_authenticated: bool = False


async def session_dependency() -> AsyncIterator[AsyncSession]:
    """A transactional database session, committed when the request succeeds."""
    async with get_database().session() as session:
        yield session


def settings_dependency() -> Settings:
    return get_settings()


def llm_dependency(request: Request) -> LLMClient:
    """The shared LLM client created during application startup."""
    client: LLMClient | None = getattr(request.app.state, "llm", None)
    if client is None:
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


def require_principal() -> Principal:
    """Authentication seam. Replace this to enforce real credentials."""
    return Principal()


SessionDep = Annotated[AsyncSession, Depends(session_dependency)]
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
