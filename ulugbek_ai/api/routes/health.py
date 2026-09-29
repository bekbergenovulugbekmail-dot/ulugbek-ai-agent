"""Health and readiness."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from ulugbek_ai import __version__
from ulugbek_ai.api.auth import configured_token_problem
from ulugbek_ai.api.deps import RegistryDep, SessionDep, SettingsDep
from ulugbek_ai.llm.claude import workspace_id_problem

router = APIRouter(tags=["health"])


def _workspace_report(workspace_id: str | None) -> dict[str, Any]:
    """Presence and shape of the workspace id, with no part of the value."""
    problem = workspace_id_problem(workspace_id)
    return {
        "configured": workspace_id is not None,
        "usable": problem is None,
        "problem": problem,
    }


def _auth_report(token: str | None) -> dict[str, Any]:
    """Whether the API can authenticate anyone at all — two booleans, no value.

    This is on the open endpoint on purpose. A server with no usable
    ``AUTH_TOKEN`` refuses every protected route, and from outside that is
    indistinguishable from a broken deployment; a caller already learns as much
    from the 503 those routes answer with, so nothing is given away by saying
    it here, where a starter script or a platform check can see it.
    """
    return {
        "configured": token is not None,
        "usable": configured_token_problem(token) is None,
    }


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
            # Whether a workspace id is set and whether it *could* be one —
            # never the value. A key spanning several workspaces is rejected
            # outright without this, and rejected just as hard with a
            # malformed one, so both need to be visible from outside.
            "workspace": _workspace_report(settings.anthropic_workspace_id),
        },
        "auth": _auth_report(
            settings.auth_token.get_secret_value()
            if settings.auth_token
            else None
        ),
        "tools": {"count": len(registry.list())},
    }


@router.get("/health/tools", summary="List registered tools and permissions")
async def tools(registry: RegistryDep) -> dict[str, Any]:
    """Expose the tool surface — useful when wiring a new integration."""
    return {"count": len(registry.list()), "tools": registry.describe_all()}
