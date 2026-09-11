"""Dashboard aggregate: counters, component health and the agent's state.

One request instead of six, because a dashboard that fires a request per tile
is slow and racy. Everything here is a count or a status — no row content, so
nothing sensitive can leak through it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from ulugbek_ai import __version__
from ulugbek_ai.agent.models import AgentRun
from ulugbek_ai.api.deps import EventServiceDep, RegistryDep, SessionDep, SettingsDep
from ulugbek_ai.approvals.models import Approval
from ulugbek_ai.core.enums import ApprovalStatus, RunStatus, TaskStatus
from ulugbek_ai.events.schemas import AgentStateSnapshot
from ulugbek_ai.memory.models import Memory
from ulugbek_ai.projects.models import Project
from ulugbek_ai.tasks.models import Task
from ulugbek_ai.tools.models import ToolExecution

router = APIRouter(prefix="/system", tags=["system"])


class ComponentHealth(BaseModel):
    """Status of one dependency."""

    name: str
    status: str = Field(description="ok | degraded | unconfigured")
    detail: str | None = None


class SystemCounters(BaseModel):
    projects: int = 0
    active_projects: int = 0
    tasks: int = 0
    active_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    pending_approvals: int = 0
    memories: int = 0
    runs: int = 0
    running_runs: int = 0
    tool_executions: int = 0


class SystemOverview(BaseModel):
    """Everything the dashboard renders above the fold."""

    version: str
    environment: str
    healthy: bool
    components: list[ComponentHealth]
    counters: SystemCounters
    agent: AgentStateSnapshot
    tasks_by_status: dict[str, int] = Field(default_factory=dict)


async def _count(session: AsyncSession, model: Any, *conditions: Any) -> int:
    statement = select(func.count()).select_from(model)
    for condition in conditions:
        statement = statement.where(condition)
    result = await session.execute(statement)
    return int(result.scalar_one())


@router.get("/overview", response_model=SystemOverview, summary="Dashboard data")
async def overview(
    session: SessionDep,
    settings: SettingsDep,
    registry: RegistryDep,
    events: EventServiceDep,
) -> SystemOverview:
    database_ok = True
    database_detail: str | None = None
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - health must never raise
        database_ok = False
        database_detail = type(exc).__name__

    counters = SystemCounters()
    tasks_by_status: dict[str, int] = {}

    if database_ok:
        counters = SystemCounters(
            projects=await _count(session, Project),
            active_projects=await _count(
                session, Project, Project.status == "ACTIVE"
            ),
            tasks=await _count(session, Task),
            active_tasks=await _count(
                session,
                Task,
                Task.status.in_(
                    [
                        TaskStatus.PENDING.value,
                        TaskStatus.PLANNING.value,
                        TaskStatus.RUNNING.value,
                        TaskStatus.WAITING_APPROVAL.value,
                    ]
                ),
            ),
            completed_tasks=await _count(
                session, Task, Task.status == TaskStatus.COMPLETED.value
            ),
            failed_tasks=await _count(
                session, Task, Task.status == TaskStatus.FAILED.value
            ),
            pending_approvals=await _count(
                session, Approval, Approval.status == ApprovalStatus.PENDING.value
            ),
            memories=await _count(session, Memory),
            runs=await _count(session, AgentRun),
            running_runs=await _count(
                session, AgentRun, AgentRun.status == RunStatus.RUNNING.value
            ),
            tool_executions=await _count(session, ToolExecution),
        )
        for status in TaskStatus:
            tasks_by_status[status.value] = await _count(
                session, Task, Task.status == status.value
            )

    llm_configured = settings.anthropic_api_key is not None
    components = [
        ComponentHealth(name="API", status="ok"),
        ComponentHealth(
            name="Database",
            status="ok" if database_ok else "degraded",
            detail=database_detail,
        ),
        ComponentHealth(
            name="Claude",
            status="ok" if llm_configured else "unconfigured",
            detail=settings.claude_model
            if llm_configured
            else "ANTHROPIC_API_KEY is not set",
        ),
        ComponentHealth(
            name="Tool Registry",
            status="ok" if registry.list() else "degraded",
            detail=f"{len(registry.list())} tools",
        ),
    ]

    return SystemOverview(
        version=__version__,
        environment=settings.environment,
        healthy=database_ok and llm_configured,
        components=components,
        counters=counters,
        agent=await events.state(),
        tasks_by_status=tasks_by_status,
    )
