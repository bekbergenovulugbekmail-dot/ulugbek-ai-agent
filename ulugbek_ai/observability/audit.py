"""Durable execution trace.

Each call appends one ``agent_steps`` row. Between them, a finished run can be
replayed step by step: plan, llm_call, tool_call, tool_result, permission,
approval, verification, error, final_result.

Every payload is redacted on the way in — the audit trail must be safe to read
and export.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.models import AgentStep
from ulugbek_ai.core.enums import StepType
from ulugbek_ai.core.redaction import redact


class AuditLogger:
    """Appends ordered trace entries for a single run."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        run_id: uuid.UUID,
        task_id: uuid.UUID | None = None,
    ) -> None:
        self._session = session
        self._run_id = run_id
        self._task_id = task_id
        self._sequence = 0

    @property
    def sequence(self) -> int:
        return self._sequence

    def set_sequence(self, value: int) -> None:
        """Continue numbering after a resumed run."""
        self._sequence = value

    async def record(
        self,
        step_type: StepType,
        *,
        summary: str,
        payload: dict[str, Any] | None = None,
        iteration: int | None = None,
        success: bool | None = None,
    ) -> AgentStep:
        """Append one trace entry."""
        self._sequence += 1
        step = AgentStep(
            agent_run_id=self._run_id,
            task_id=self._task_id,
            sequence=self._sequence,
            iteration=iteration,
            type=step_type,
            summary=summary[:2_000],
            payload=redact(payload or {}),
            success=success,
        )
        self._session.add(step)
        await self._session.flush()
        # Checkpoint the run here rather than at the end.
        #
        # A run is a long-lived process, not an atomic unit: its progress has to
        # be visible to other sessions *while it is still going* — that is what
        # makes the live event stream live, and what lets a crashed run be
        # resumed from its last recorded step. Every state change the outside
        # world cares about passes through this method, so committing here (and
        # only here) keeps that guarantee in one place.
        await self._session.commit()
        return step

    # -- Convenience wrappers, one per trace category ---------------------- #
    async def plan(self, summary: str, payload: dict[str, Any], *, replan: bool = False):
        return await self.record(
            StepType.REPLAN if replan else StepType.PLAN,
            summary=summary,
            payload=payload,
        )

    async def llm_call(self, summary: str, payload: dict[str, Any], iteration: int):
        return await self.record(
            StepType.LLM_CALL, summary=summary, payload=payload, iteration=iteration
        )

    async def tool_call(self, summary: str, payload: dict[str, Any], iteration: int):
        return await self.record(
            StepType.TOOL_CALL, summary=summary, payload=payload, iteration=iteration
        )

    async def tool_result(
        self, summary: str, payload: dict[str, Any], iteration: int, *, success: bool
    ):
        return await self.record(
            StepType.TOOL_RESULT,
            summary=summary,
            payload=payload,
            iteration=iteration,
            success=success,
        )

    async def permission(self, summary: str, payload: dict[str, Any]):
        return await self.record(
            StepType.PERMISSION, summary=summary, payload=payload
        )

    async def approval(self, summary: str, payload: dict[str, Any]):
        return await self.record(StepType.APPROVAL, summary=summary, payload=payload)

    async def verification(
        self, summary: str, payload: dict[str, Any], *, success: bool
    ):
        return await self.record(
            StepType.VERIFICATION,
            summary=summary,
            payload=payload,
            success=success,
        )

    async def error(self, summary: str, payload: dict[str, Any] | None = None):
        return await self.record(
            StepType.ERROR, summary=summary, payload=payload, success=False
        )

    async def final_result(self, summary: str, payload: dict[str, Any], *, success: bool):
        return await self.record(
            StepType.FINAL_RESULT,
            summary=summary,
            payload=payload,
            success=success,
        )
