"""Aggregate router."""

from __future__ import annotations

from fastapi import APIRouter

from ulugbek_ai.api.routes import agent, approvals, health, memory, projects, tasks

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(agent.router)
api_router.include_router(projects.router)
api_router.include_router(tasks.router)
api_router.include_router(memory.router)
api_router.include_router(approvals.router)

__all__ = ["api_router"]
