"""Task endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from ulugbek_ai.api.deps import PrincipalDep, SessionDep
from ulugbek_ai.core.enums import TaskStatus
from ulugbek_ai.tasks.manager import TaskManager
from ulugbek_ai.tasks.models import Task
from ulugbek_ai.tasks.schemas import TaskCreate, TaskRead

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=list[TaskRead], summary="List tasks")
async def list_tasks(
    session: SessionDep,
    status_filter: TaskStatus | None = Query(default=None, alias="status"),
    project_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Task]:
    return await TaskManager(session).list(
        status=status_filter, project_id=project_id, limit=limit, offset=offset
    )


@router.post("", response_model=TaskRead, summary="Create a task")
async def create_task(
    payload: TaskCreate, session: SessionDep, principal: PrincipalDep
) -> Task:
    """Create a task without running it — the agent can pick it up later."""
    return await TaskManager(session).create(payload)


@router.get("/{task_id}", response_model=TaskRead, summary="Read a task")
async def get_task(task_id: uuid.UUID, session: SessionDep) -> Task:
    return await TaskManager(session).get(task_id)


@router.post("/{task_id}/cancel", response_model=TaskRead, summary="Cancel a task")
async def cancel_task(
    task_id: uuid.UUID,
    session: SessionDep,
    principal: PrincipalDep,
    reason: str | None = None,
) -> Task:
    return await TaskManager(session).cancel(task_id, reason=reason)
