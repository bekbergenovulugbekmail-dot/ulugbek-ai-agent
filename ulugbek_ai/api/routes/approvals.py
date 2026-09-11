"""Approval endpoints — the human half of the loop."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from ulugbek_ai.agent.schemas import AgentRunResponse
from ulugbek_ai.api.deps import (
    EngineDep,
    OptionalRunnerDep,
    PrincipalDep,
    SessionDep,
)
from ulugbek_ai.agent.repository import AgentRunRepository
from ulugbek_ai.approvals.manager import ApprovalManager
from ulugbek_ai.approvals.models import Approval
from ulugbek_ai.approvals.schemas import ApprovalDecision, ApprovalRead
from ulugbek_ai.core.enums import ApprovalStatus
from ulugbek_ai.core.errors import ConfigurationError, NotFoundError

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[ApprovalRead], summary="List approvals")
async def list_approvals(
    session: SessionDep,
    status_filter: ApprovalStatus | None = Query(default=None, alias="status"),
    run_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Approval]:
    return await ApprovalManager(session).list(
        status=status_filter, run_id=run_id, limit=limit, offset=offset
    )


@router.get("/{approval_id}", response_model=ApprovalRead, summary="Read an approval")
async def get_approval(approval_id: uuid.UUID, session: SessionDep) -> Approval:
    return await ApprovalManager(session).get(approval_id)


@router.post(
    "/{approval_id}/approve",
    response_model=AgentRunResponse,
    summary="Approve a gated action and continue the run",
)
async def approve(
    approval_id: uuid.UUID,
    decision: ApprovalDecision,
    session: SessionDep,
    engine: EngineDep,
    runner: OptionalRunnerDep,
    principal: PrincipalDep,
    background: bool = Query(
        default=False,
        description="Resume in the background and return the run immediately.",
    ),
) -> AgentRunResponse:
    """Record the approval, then resume the paused run.

    Approving is what unblocks the run: the engine picks up exactly the tool
    call that was gated and carries on from there. With ``background=true`` the
    resumed run is handed to the runner so a UI can follow it on the event
    stream instead of waiting on this request.
    """
    manager = ApprovalManager(session)
    approval = await manager.approve(
        approval_id,
        decided_by=decision.decided_by or principal.subject,
        note=decision.note,
    )
    if background:
        return await _resume_in_background(
            session, runner, approval.agent_run_id
        )
    return await engine.resume(approval.agent_run_id)


@router.post(
    "/{approval_id}/reject",
    response_model=AgentRunResponse,
    summary="Reject a gated action and let the run continue without it",
)
async def reject(
    approval_id: uuid.UUID,
    decision: ApprovalDecision,
    session: SessionDep,
    engine: EngineDep,
    runner: OptionalRunnerDep,
    principal: PrincipalDep,
    background: bool = Query(
        default=False,
        description="Resume in the background and return the run immediately.",
    ),
) -> AgentRunResponse:
    """Record the rejection and resume.

    The run is not killed: the agent is told the action was refused and decides
    how to proceed, which is usually to explain the situation to the user.
    """
    manager = ApprovalManager(session)
    approval = await manager.reject(
        approval_id,
        decided_by=decision.decided_by or principal.subject,
        note=decision.note,
    )
    if background:
        return await _resume_in_background(
            session, runner, approval.agent_run_id
        )
    return await engine.resume(approval.agent_run_id)


async def _resume_in_background(
    session, runner, run_id: uuid.UUID
) -> AgentRunResponse:
    """Commit the decision, then let the runner continue the run."""
    if runner is None:
        raise ConfigurationError(
            "Background resume needs the agent runner. Set ANTHROPIC_API_KEY "
            "and restart the application, or call without background=true."
        )
    await session.commit()
    runner.launch(run_id, resume=True)
    run = await AgentRunRepository(session).get(run_id)
    if run is None:  # pragma: no cover - the approval guarantees it exists
        raise NotFoundError(
            f"Agent run {run_id} not found.", details={"run_id": str(run_id)}
        )
    return AgentRunResponse.snapshot(run)
