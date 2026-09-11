"""Health and readiness."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from ulugbek_ai import __version__
from ulugbek_ai.api.deps import RegistryDep, SessionDep, SettingsDep

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness and dependency check")
async def health(
    session: SessionDep, settings: SettingsDep, registry: RegistryDep
) -> dict[str, Any]:
    """Report service health.

    The database is probed for real; the LLM is reported as *configured* rather
    than called, so a health check never costs a token.
    """
    database_ok = True
    database_error: str | None = None
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - health must never raise
        database_ok = False
        database_error = type(exc).__name__

    return {
        "status": "ok" if database_ok else "degraded",
        "version": __version__,
        "environment": settings.environment,
        "database": {"connected": database_ok, "error": database_error},
        "llm": {
            "configured": settings.anthropic_api_key is not None,
            "model": settings.claude_model,
        },
        "tools": {"count": len(registry.list())},
    }


@router.get("/health/tools", summary="List registered tools and permissions")
async def tools(registry: RegistryDep) -> dict[str, Any]:
    """Expose the tool surface — useful when wiring a new integration."""
    return {"count": len(registry.list()), "tools": registry.describe_all()}
