"""Approval lifecycle."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.approvals.models import Approval
from ulugbek_ai.approvals.repository import ApprovalRepository
from ulugbek_ai.core.enums import ApprovalStatus, PermissionLevel
from ulugbek_ai.core.errors import ConflictError, NotFoundError
from ulugbek_ai.core.redaction import redact
from ulugbek_ai.core.utils import ensure_utc, utcnow

#: A pending approval older than this is considered stale.
DEFAULT_APPROVAL_TTL = timedelta(days=7)


class ApprovalManager:
    """Requests approvals and records human decisions."""

    def __init__(
        self, session: AsyncSession, *, ttl: timedelta = DEFAULT_APPROVAL_TTL
    ) -> None:
        self._session = session
        self._repository = ApprovalRepository(session)
        self._ttl = ttl

    async def request(
        self,
        *,
        tool_name: str,
        tool_arguments: dict,
        permission: PermissionLevel,
        reason: str,
        goal: str | None = None,
        run_id: uuid.UUID | None = None,
        task_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        requested_by: uuid.UUID | None = None,
        tool_call_id: str | None = None,
    ) -> Approval:
        """Create a pending approval. Arguments are redacted before storage."""
        approval = Approval(
            tool_name=tool_name,
            tool_arguments=redact(tool_arguments),
            permission=permission,
            reason=reason,
            goal=goal,
            agent_run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            requested_by=requested_by,
            tool_call_id=tool_call_id,
            expires_at=utcnow() + self._ttl,
        )
        self._repository.add(approval)
        await self._repository.flush()
        return approval

    async def get(self, approval_id: uuid.UUID) -> Approval:
        approval = await self._repository.get(approval_id)
        if approval is None:
            raise NotFoundError(
                f"Approval {approval_id} not found.",
                details={"approval_id": str(approval_id)},
            )
        return approval

    async def list(self, **kwargs) -> list[Approval]:
        return await self._repository.list(**kwargs)

    async def get_pending_for_run(self, run_id: uuid.UUID) -> Approval | None:
        return await self._repository.get_pending_for_run(run_id)

    async def approve(
        self,
        approval_id: uuid.UUID,
        *,
        decided_by: str | None = None,
        note: str | None = None,
    ) -> Approval:
        return await self._decide(
            approval_id, ApprovalStatus.APPROVED, decided_by=decided_by, note=note
        )

    async def reject(
        self,
        approval_id: uuid.UUID,
        *,
        decided_by: str | None = None,
        note: str | None = None,
    ) -> Approval:
        return await self._decide(
            approval_id, ApprovalStatus.REJECTED, decided_by=decided_by, note=note
        )

    async def _decide(
        self,
        approval_id: uuid.UUID,
        status: ApprovalStatus,
        *,
        decided_by: str | None,
        note: str | None,
    ) -> Approval:
        approval = await self.get(approval_id)

        if approval.status != ApprovalStatus.PENDING:
            raise ConflictError(
                f"Approval {approval_id} was already {approval.status}.",
                details={
                    "approval_id": str(approval_id),
                    "status": str(approval.status),
                },
            )

        expires_at = ensure_utc(approval.expires_at)
        if expires_at is not None and expires_at < utcnow():
            approval.status = ApprovalStatus.EXPIRED
            approval.decided_at = utcnow()
            await self._repository.flush()
            raise ConflictError(
                f"Approval {approval_id} expired at {expires_at.isoformat()}.",
                details={"approval_id": str(approval_id)},
            )

        approval.status = status
        approval.decided_by = decided_by
        approval.decision_note = note
        approval.decided_at = utcnow()
        await self._repository.flush()
        return approval
