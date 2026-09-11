"""Project endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from ulugbek_ai.api.deps import PrincipalDep, SessionDep
from ulugbek_ai.core.enums import ProjectStatus
from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.models import Project
from ulugbek_ai.projects.schemas import ProjectCreate, ProjectRead, ProjectUpdate

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
