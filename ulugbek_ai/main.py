"""Application entrypoint.

Long-lived, expensive objects (the LLM client, the tool registry) are built once
during startup and stored on ``app.state``; per-request objects (sessions, the
engine) come from dependencies. That split is what keeps the request path cheap
and the wiring testable.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ulugbek_ai import __version__
from ulugbek_ai.api.errors import register_exception_handlers
from ulugbek_ai.api.router import api_router
from ulugbek_ai.config.settings import Settings, get_settings
from ulugbek_ai.database.session import get_database, reset_database
from ulugbek_ai.llm.claude import ClaudeClient
from ulugbek_ai.observability.logger import configure_logging
from ulugbek_ai.tools.permissions import PermissionPolicy, PermissionService
from ulugbek_ai.tools.registry import build_default_registry

logger = logging.getLogger(__name__)

DESCRIPTION = """\
ULUGBEK AI — a universal, modular AI agent core.

The agent plans, selects and executes tools under a permission policy, verifies
its own results and pauses for human approval before dangerous actions.
"""


def build_llm_client(settings: Settings) -> ClaudeClient | None:
    """Construct the Claude client, or ``None`` when no key is configured.

    A missing key is not fatal: the service still starts and serves the health,
    project, task, memory and approval endpoints. Only ``/agent/run`` requires
    the model, and it reports a clear configuration error.
    """
    if settings.anthropic_api_key is None:
        logger.warning(
            "ANTHROPIC_API_KEY is not set — agent endpoints will return a "
            "configuration error until it is provided."
        )
        return None
    return ClaudeClient.from_settings(settings)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start up and tear down shared resources."""
    settings: Settings = app.state.settings

    app.state.llm = build_llm_client(settings)
    app.state.registry = build_default_registry(
        permissions=PermissionService(PermissionPolicy.from_settings(settings)),
        default_timeout_seconds=settings.tool_default_timeout_seconds,
        max_result_chars=settings.tool_max_result_chars,
    )
    get_database(settings)

    logger.info(
        "%s %s started in %s with %d tool(s)",
        settings.app_name,
        __version__,
        settings.environment,
        len(app.state.registry.list()),
    )
    try:
        yield
    finally:
        if app.state.llm is not None:
            await app.state.llm.aclose()
        await reset_database()
        logger.info("Shutdown complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory."""
    resolved = settings or get_settings()
    configure_logging(level=resolved.log_level, json_output=resolved.log_json)

    app = FastAPI(
        title=resolved.app_name,
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = resolved

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=resolved.api_prefix)

    @app.get("/", tags=["health"], summary="Service banner")
    async def root() -> dict[str, str]:
        return {
            "name": resolved.app_name,
            "version": __version__,
            "docs": "/docs",
            "api": resolved.api_prefix,
        }

    return app


app = create_app()
