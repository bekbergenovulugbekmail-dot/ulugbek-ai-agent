"""Approval lifecycle."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.approvals.manager import ApprovalManager
from ulugbek_ai.core.enums import ApprovalStatus, PermissionLevel
from ulugbek_ai.core.errors import ConflictError, NotFoundError


async def _pending(session: AsyncSession, **kwargs):
    return await ApprovalManager(session).request(
        tool_name="deploy",
        tool_arguments={"env": "production", "api_key": "sk-ant-0123456789abcdef"},
        permission=PermissionLevel.CRITICAL,
        reason="CRITICAL actions require explicit human approval.",
        goal="Deploy to production",
        **kwargs,
    )


async def test_request_redacts_arguments(session: AsyncSession) -> None:
    """An approval row is shown to a human, so it must carry no secrets."""
    approval = await _pending(session)

    assert approval.status is ApprovalStatus.PENDING
    assert approval.tool_arguments["env"] == "production"
    assert approval.tool_arguments["api_key"] == "***REDACTED***"


async def test_approve(session: AsyncSession) -> None:
    manager = ApprovalManager(session)
    approval = await _pending(session)

    decided = await manager.approve(approval.id, decided_by="ulugbek", note="ok")

    assert decided.status is ApprovalStatus.APPROVED
    assert decided.decided_by == "ulugbek"
    assert decided.decided_at is not None


async def test_reject(session: AsyncSession) -> None:
    manager = ApprovalManager(session)
    approval = await _pending(session)

    decided = await manager.reject(approval.id, decided_by="ulugbek")

    assert decided.status is ApprovalStatus.REJECTED


async def test_deciding_twice_is_rejected(session: AsyncSession) -> None:
    manager = ApprovalManager(session)
    approval = await _pending(session)
    await manager.approve(approval.id)

    with pytest.raises(ConflictError):
        await manager.approve(approval.id)
    with pytest.raises(ConflictError):
        await manager.reject(approval.id)


async def test_an_expired_approval_cannot_be_used(session: AsyncSession) -> None:
    manager = ApprovalManager(session, ttl=timedelta(seconds=-1))
    approval = await manager.request(
        tool_name="deploy",
        tool_arguments={},
        permission=PermissionLevel.CRITICAL,
        reason="needs approval",
    )

    with pytest.raises(ConflictError, match="expired"):
        await manager.approve(approval.id)

    assert (await manager.get(approval.id)).status is ApprovalStatus.EXPIRED


async def test_unknown_approval(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await ApprovalManager(session).get(uuid.uuid4())


async def test_listing_by_status(session: AsyncSession) -> None:
    manager = ApprovalManager(session)
    first = await _pending(session)
    await _pending(session)
    await manager.approve(first.id)

    pending = await manager.list(status=ApprovalStatus.PENDING)
    approved = await manager.list(status=ApprovalStatus.APPROVED)

    assert len(pending) == 1
    assert len(approved) == 1
