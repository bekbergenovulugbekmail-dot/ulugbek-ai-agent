"""Project endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from pydantic import BaseModel, Field

from ulugbek_ai.api.deps import EventServiceDep, PrincipalDep, SessionDep
from ulugbek_ai.core.enums import ProjectStatus
from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.models import Project
from ulugbek_ai.projects.schemas import ProjectCreate, ProjectRead, ProjectUpdate
from ulugbek_ai.events.schemas import AgentEvent
from ulugbek_ai.memory.manager import MemoryManager
from ulugbek_ai.memory.schemas import MemoryRead
from ulugbek_ai.tasks.manager import TaskManager
from ulugbek_ai.tasks.schemas import TaskRead

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectRead], summary="List projects")
async def list_projects(
    session: SessionDep,
    status_filter: ProjectStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Project]:
    return await ProjectManager(session).list(
        status=status_filter, limit=limit, offset=offset
    )


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
async def create_project(
    payload: ProjectCreate, session: SessionDep, principal: PrincipalDep
) -> Project:
    return await ProjectManager(session).create(payload)


@router.get("/{project_id}", response_model=ProjectRead, summary="Read a project")
async def get_project(project_id: uuid.UUID, session: SessionDep) -> Project:
    return await ProjectManager(session).get(project_id)


@router.patch(
    "/{project_id}", response_model=ProjectRead, summary="Update a project"
)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    session: SessionDep,
    principal: PrincipalDep,
) -> Project:
    return await ProjectManager(session).update(project_id, payload)


class ProjectOverview(BaseModel):
    """A project plus everything its detail page shows.

    Served as one request so the page does not fan out into five.
    """

    project: ProjectRead
    tasks: list[TaskRead] = Field(default_factory=list)
    memories: list[MemoryRead] = Field(default_factory=list)
    activity: list[AgentEvent] = Field(default_factory=list)
    integrations: list[str] = Field(default_factory=list)


@router.get(
    "/{project_id}/overview",
    response_model=ProjectOverview,
    summary="A project with its tasks, memory and recent activity",
)
async def project_overview(
    project_id: uuid.UUID,
    session: SessionDep,
    events: EventServiceDep,
    task_limit: int = Query(default=10, ge=1, le=50),
    memory_limit: int = Query(default=10, ge=1, le=50),
    activity_limit: int = Query(default=20, ge=1, le=100),
) -> ProjectOverview:
    project = await ProjectManager(session).get(project_id)
    tasks = await TaskManager(session).list(project_id=project_id, limit=task_limit)
    memories = await MemoryManager(session).list_memories(
        project_id=project_id, limit=memory_limit
    )
    activity = await events.recent(project_id=project_id, limit=activity_limit)

    return ProjectOverview(
        project=ProjectRead.model_validate(project),
        tasks=[TaskRead.model_validate(task) for task in tasks],
        memories=[MemoryRead.model_validate(memory) for memory in memories],
        activity=activity.events,
        integrations=sorted(project.integrations or {}),
    )
