"""Agent endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Query

from ulugbek_ai.agent.repository import AgentRunRepository
from ulugbek_ai.agent.schemas import (
    AgentRunDetail,
    AgentRunRead,
    AgentRunRequest,
    AgentRunResponse,
    AgentStepRead,
)
from ulugbek_ai.api.deps import (
    EngineDep,
    PrincipalDep,
    RunnerDep,
    SessionDep,
)
from ulugbek_ai.core.enums import RunStatus
from ulugbek_ai.core.errors import NotFoundError

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/run", response_model=AgentRunResponse, summary="Run the agent")
async def run_agent(
    payload: AgentRunRequest, engine: EngineDep, principal: PrincipalDep
) -> AgentRunResponse:
    """Execute a request through the full agent loop.

    The response is terminal (``COMPLETED`` / ``FAILED``) or ``WAITING_APPROVAL``
    with the approval that must be decided before the run can continue.
    """
    return await engine.run(payload)


@router.post(
    "/runs",
    response_model=AgentRunResponse,
    status_code=202,
    summary="Start a run in the background and return immediately",
)
async def start_agent_run(
    payload: AgentRunRequest,
    engine: EngineDep,
    runner: RunnerDep,
    session: SessionDep,
    principal: PrincipalDep,
) -> AgentRunResponse:
    """Create the run, hand it to the background runner, and return its id.

    This is the endpoint a user interface uses: the caller gets ``run_id``
    straight away and follows the work on ``/events/runs/{run_id}/stream``,
    instead of holding a request open for the length of the run.
    """
    run, resolution = await engine.start(payload)
    # Commit before launching: the background task opens its own session and
    # must be able to see the row.
    await session.commit()
    runner.launch(run.id)
    return AgentRunResponse.snapshot(run, resolution.task)


@router.post(
    "/runs/{run_id}/resume",
    response_model=AgentRunResponse,
    summary="Resume a run that was waiting for approval",
)
async def resume_run(
    run_id: uuid.UUID, engine: EngineDep, principal: PrincipalDep
) -> AgentRunResponse:
    return await engine.resume(run_id)


@router.get("/runs", response_model=list[AgentRunRead], summary="List agent runs")
async def list_runs(
    session: SessionDep,
    status: RunStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Any]:
    return await AgentRunRepository(session).list(
        status=status, limit=limit, offset=offset
    )


@router.get(
    "/runs/{run_id}",
    response_model=AgentRunDetail,
    summary="Read a run with its full execution trace",
)
async def get_run(run_id: uuid.UUID, session: SessionDep) -> AgentRunDetail:
    repository = AgentRunRepository(session)
    run = await repository.get(run_id)
    if run is None:
        raise NotFoundError(
            f"Agent run {run_id} not found.", details={"run_id": str(run_id)}
        )
    steps = await repository.list_steps(run_id)
    detail = AgentRunDetail.model_validate(run)
    detail.steps = [AgentStepRead.model_validate(step) for step in steps]
    return detail
