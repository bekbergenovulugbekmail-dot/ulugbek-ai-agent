"""Data access for approvals."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.approvals.models import Approval
from ulugbek_ai.core.enums import ApprovalStatus


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, approval: Approval) -> Approval:
        self._session.add(approval)
        return approval

    async def flush(self) -> None:
        await self._session.flush()

    async def get(self, approval_id: uuid.UUID) -> Approval | None:
        return await self._session.get(Approval, approval_id)

    async def list(
        self,
        *,
        status: ApprovalStatus | None = None,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Approval]:
        statement = select(Approval)
        if status is not None:
            statement = statement.where(Approval.status == status)
        if run_id is not None:
            statement = statement.where(Approval.agent_run_id == run_id)
        if task_id is not None:
            statement = statement.where(Approval.task_id == task_id)
        statement = (
            statement.order_by(Approval.created_at.desc()).limit(limit).offset(offset)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def get_pending_for_run(self, run_id: uuid.UUID) -> Approval | None:
        result = await self._session.execute(
            select(Approval)
            .where(
                Approval.agent_run_id == run_id,
                Approval.status == ApprovalStatus.PENDING,
            )
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
