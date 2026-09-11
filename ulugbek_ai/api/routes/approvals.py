"""Approval endpoints — the human half of the loop."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from ulugbek_ai.agent.schemas import AgentRunResponse
from ulugbek_ai.api.deps import EngineDep, PrincipalDep, SessionDep
from ulugbek_ai.approvals.manager import ApprovalManager
from ulugbek_ai.approvals.models import Approval
from ulugbek_ai.approvals.schemas import ApprovalDecision, ApprovalRead
from ulugbek_ai.core.enums import ApprovalStatus

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
    principal: PrincipalDep,
) -> AgentRunResponse:
    """Record the approval, then resume the paused run.

    Approving is what unblocks the run: the engine picks up exactly the tool
    call that was gated and carries on from there.
    """
    manager = ApprovalManager(session)
    approval = await manager.approve(
        approval_id,
        decided_by=decision.decided_by or principal.subject,
        note=decision.note,
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
    principal: PrincipalDep,
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
    return await engine.resume(approval.agent_run_id)
