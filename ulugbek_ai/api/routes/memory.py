"""Memory endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from ulugbek_ai.api.deps import PrincipalDep, SessionDep
from ulugbek_ai.core.enums import MemoryType
from ulugbek_ai.memory.manager import MemoryManager
from ulugbek_ai.memory.models import Memory
from ulugbek_ai.memory.schemas import (
    MemoryCreate,
    MemoryRead,
    MemorySearchResult,
    MemoryUpdate,
)

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("", response_model=list[MemoryRead], summary="List memories")
async def list_memories(
    session: SessionDep,
    type_filter: MemoryType | None = Query(default=None, alias="type"),
    project_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Memory]:
    return await MemoryManager(session).list_memories(
        types=[type_filter] if type_filter else None,
        project_id=project_id,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/search",
    response_model=list[MemorySearchResult],
    summary="Search memories by relevance",
)
async def search_memories(
    session: SessionDep,
    q: str = Query(min_length=1, max_length=2_000, description="Search text"),
    project_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[MemorySearchResult]:
    results = await MemoryManager(session).search_memory(
        q, project_id=project_id, user_id=user_id, limit=limit
    )
    return [
        MemorySearchResult(
            memory=MemoryRead.model_validate(item.memory), score=item.score
        )
        for item in results
    ]


@router.post(
    "",
    response_model=MemoryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Store a memory",
)
async def create_memory(
    payload: MemoryCreate, session: SessionDep, principal: PrincipalDep
) -> Memory:
    return await MemoryManager(session).save_memory(payload)


@router.get("/{memory_id}", response_model=MemoryRead, summary="Read a memory")
async def get_memory(memory_id: uuid.UUID, session: SessionDep) -> Memory:
    return await MemoryManager(session).get_memory(memory_id)


@router.patch("/{memory_id}", response_model=MemoryRead, summary="Update a memory")
async def update_memory(
    memory_id: uuid.UUID,
    payload: MemoryUpdate,
    session: SessionDep,
    principal: PrincipalDep,
) -> Memory:
    return await MemoryManager(session).update_memory(memory_id, payload)


@router.delete(
    "/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a memory",
)
async def delete_memory(
    memory_id: uuid.UUID, session: SessionDep, principal: PrincipalDep
) -> None:
    await MemoryManager(session).delete_memory(memory_id)
