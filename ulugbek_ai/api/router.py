"""Aggregate router."""

from __future__ import annotations

from fastapi import APIRouter

from ulugbek_ai.api.routes import (
    agent,
    approvals,
    events,
    health,
    memory,
    projects,
    system,
    tasks,
    tools,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(agent.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
api_router.include_router(memory.router)
api_router.include_router(approvals.router)
api_router.include_router(events.router)
api_router.include_router(tools.router)
api_router.include_router(system.router)

__all__ = ["api_router"]
