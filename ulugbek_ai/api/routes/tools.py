"""Tool registry and tool execution history."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from ulugbek_ai.api.deps import RegistryDep, SessionDep
from ulugbek_ai.core.enums import (
    PermissionLevel,
    ToolExecutionStatus,
    VerificationStatus,
)
from ulugbek_ai.tools.models import ToolExecution
from ulugbek_ai.tools.repository import ToolExecutionRepository

router = APIRouter(prefix="/tools", tags=["tools"])


class ToolExecutionRead(BaseModel):
    """One recorded tool invocation.

    ``arguments`` and ``output`` were redacted when the row was written, so this
    is safe to render.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str
    service: str | None
    status: ToolExecutionStatus
    permission: PermissionLevel
    arguments: dict[str, Any]
    output: dict[str, Any] | None
    error: str | None
    duration_ms: int
    verification_status: VerificationStatus | None
    verification_reason: str | None
    agent_run_id: uuid.UUID | None
    task_id: uuid.UUID | None
    created_at: datetime


@router.get("", summary="List registered tools")
async def list_tools(registry: RegistryDep) -> dict[str, Any]:
    """The tool surface, with each tool's permission level."""
    return {"count": len(registry.list()), "tools": registry.describe_all()}


@router.get(
    "/executions",
    response_model=list[ToolExecutionRead],
    summary="Tool execution history",
)
async def list_executions(
    session: SessionDep,
    run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    tool_name: str | None = None,
    service: str | None = Query(default=None, description="Filter by provider."),
    status_filter: ToolExecutionStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ToolExecution]:
    return await ToolExecutionRepository(session).list(
        run_id=run_id,
        task_id=task_id,
        tool_name=tool_name,
        service=service,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
