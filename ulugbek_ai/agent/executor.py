"""Tool execution phase of the agent loop.

This module owns the awkward part of a real agent: the model may request several
tools in one turn, and any of them may need a human's approval. The provider
requires that *every* ``tool_use`` block gets a matching ``tool_result`` in the
next turn, so a run cannot simply stop halfway.

The approach: results computed before the gated call are parked on the run
(``extra['partial_tool_results']``), the run pauses, and on resume the already
executed calls are replayed from that record while the approved call finally
runs. The transcript the model sees is therefore always well-formed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.models import AgentRun
from ulugbek_ai.approvals.manager import ApprovalManager
from ulugbek_ai.approvals.models import Approval
from ulugbek_ai.core.enums import (
    ApprovalStatus,
    PermissionLevel,
    ToolExecutionStatus,
    VerificationStatus,
)
from ulugbek_ai.core.errors import (
    PermissionDeniedError,
    ToolInputError,
    ToolNotFoundError,
)
from ulugbek_ai.core.redaction import redact, truncate
from ulugbek_ai.core.utils import utcnow
from ulugbek_ai.llm.base import ContentBlock, LLMToolCall
from ulugbek_ai.observability.audit import AuditLogger
from ulugbek_ai.tools.base import ToolContext, ToolResult, ToolVerification
from ulugbek_ai.tools.models import ToolExecution
from ulugbek_ai.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

#: Key under ``AgentRun.extra`` holding results computed before a pause.
PARTIAL_RESULTS_KEY = "partial_tool_results"

_MAX_TOOL_RESULT_CHARS = 8_000


@dataclass(slots=True)
class ToolInvocation:
    """Record of one tool call within an iteration."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    ok: bool
    summary: str
    verification: ToolVerification | None = None
    replayed: bool = False


@dataclass(slots=True)
class ToolPhaseResult:
    """Outcome of resolving all tool calls in one model turn."""

    #: ``tool_result`` blocks to send back, one per requested call.
    result_blocks: list[ContentBlock] = field(default_factory=list)
    invocations: list[ToolInvocation] = field(default_factory=list)
    #: Set when the run must pause for a human decision.
    pending_approval: Approval | None = None

    @property
    def paused(self) -> bool:
        return self.pending_approval is not None


class Executor:
    """Resolves the model's tool calls, enforcing permissions and approvals."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        registry: ToolRegistry,
        approvals: ApprovalManager,
        audit: AuditLogger,
    ) -> None:
        self._session = session
        self._registry = registry
        self._approvals = approvals
        self._audit = audit

    async def resolve_tool_calls(
        self,
        run: AgentRun,
        calls: list[LLMToolCall],
        context: ToolContext,
        *,
        iteration: int,
        goal: str,
    ) -> ToolPhaseResult:
        """Execute every requested call, pausing at the first gated one."""
        outcome = ToolPhaseResult()
        partial: dict[str, Any] = dict((run.extra or {}).get(PARTIAL_RESULTS_KEY, {}))

        for call in calls:
            if call.id in partial:
                stored = partial[call.id]
                outcome.result_blocks.append(
                    _result_block(call.id, stored["content"], not stored["ok"])
                )
                outcome.invocations.append(
                    ToolInvocation(
                        call_id=call.id,
                        tool_name=call.name,
                        arguments=call.arguments,
                        ok=bool(stored["ok"]),
                        summary=truncate(str(stored["content"]), 500),
                        replayed=True,
                    )
                )
                continue

            resolved = await self._resolve_single(
                run, call, context, iteration=iteration, goal=goal
            )

            if resolved.pending_approval is not None:
                # Park what has been computed so far and stop here.
                extra = dict(run.extra or {})
                extra[PARTIAL_RESULTS_KEY] = partial
                run.extra = extra
                outcome.pending_approval = resolved.pending_approval
                return outcome

            block = resolved.result_blocks[0]
            invocation = resolved.invocations[0]
            outcome.result_blocks.append(block)
            outcome.invocations.append(invocation)
            partial[call.id] = {
                "content": block.get("content", ""),
                "ok": not block.get("is_error", False),
            }

        # Every call in this turn is resolved; the parked record is spent.
        if (run.extra or {}).get(PARTIAL_RESULTS_KEY):
            extra = dict(run.extra or {})
            extra.pop(PARTIAL_RESULTS_KEY, None)
            run.extra = extra

        return outcome

    # ------------------------------------------------------------- internals #
    async def _resolve_single(
        self,
        run: AgentRun,
        call: LLMToolCall,
        context: ToolContext,
        *,
        iteration: int,
        goal: str,
    ) -> ToolPhaseResult:
        await self._audit.tool_call(
            f"Tool requested: {call.name}",
            {"tool": call.name, "arguments": call.arguments, "call_id": call.id},
            iteration,
        )

        try:
            tool = self._registry.get(call.name)
        except ToolNotFoundError as exc:
            return self._error_outcome(call, exc.message, permission=None)

        decision_state = await self._approval_state(run, call.id)

        if decision_state is ApprovalStatus.REJECTED:
            message = (
                f"The user rejected the '{call.name}' action. "
                "Do not retry it; explain the situation or choose another approach."
            )
            await self._record_execution(
                run, call, tool.permission, ToolExecutionStatus.DENIED, error=message
            )
            await self._audit.approval(
                f"Approval rejected for {call.name}",
                {"tool": call.name, "call_id": call.id},
            )
            return self._error_outcome(call, message, permission=tool.permission)

        approved = decision_state is ApprovalStatus.APPROVED

        try:
            result = await self._registry.execute(
                call.name, call.arguments, context, approved=approved
            )
        except ToolInputError as exc:
            await self._record_execution(
                run, call, tool.permission, ToolExecutionStatus.FAILED,
                error=exc.message,
            )
            return self._error_outcome(call, exc.message, permission=tool.permission)
        except PermissionDeniedError as exc:
            if not exc.details.get("requires_approval"):
                await self._record_execution(
                    run, call, tool.permission, ToolExecutionStatus.DENIED,
                    error=exc.message,
                )
                await self._audit.permission(
                    f"Denied: {call.name}", {"tool": call.name, **exc.details}
                )
                return self._error_outcome(
                    call,
                    f"Policy denies this action: {exc.message}",
                    permission=tool.permission,
                )

            approval = await self._request_approval(
                run, call, tool.permission, reason=exc.message, goal=goal
            )
            await self._audit.approval(
                f"Approval required for {call.name}",
                {
                    "tool": call.name,
                    "call_id": call.id,
                    "approval_id": str(approval.id),
                    "permission": tool.permission.value,
                },
            )
            return ToolPhaseResult(pending_approval=approval)

        verification = await self._registry.verify(
            call.name, call.arguments, result, context
        )

        status = (
            ToolExecutionStatus.SUCCESS if result.ok else ToolExecutionStatus.FAILED
        )
        if not result.ok and "timed out" in (result.error or ""):
            status = ToolExecutionStatus.TIMEOUT

        await self._record_execution(
            run,
            call,
            tool.permission,
            status,
            result=result,
            verification=verification,
        )

        payload = (
            _stringify(result.output)
            if result.ok
            else f"ERROR: {result.error or 'tool failed'}"
        )
        if verification.status is VerificationStatus.FAILURE:
            payload = (
                f"{payload}\n\n[verification failed] {verification.reason}"
            )

        await self._audit.tool_result(
            f"Tool {call.name}: {'ok' if result.ok else 'failed'}",
            {
                "tool": call.name,
                "call_id": call.id,
                "result": result.to_dict(),
                "verification": {
                    "status": verification.status.value,
                    "reason": verification.reason,
                },
            },
            iteration,
            success=result.ok,
        )

        ok = result.ok and verification.status is not VerificationStatus.FAILURE
        return ToolPhaseResult(
            result_blocks=[_result_block(call.id, payload, not ok)],
            invocations=[
                ToolInvocation(
                    call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    ok=ok,
                    summary=truncate(payload, 500),
                    verification=verification,
                )
            ],
        )

    async def _approval_state(
        self, run: AgentRun, call_id: str
    ) -> ApprovalStatus | None:
        """Latest decision recorded for this specific tool call, if any."""
        approvals = await self._approvals.list(run_id=run.id, limit=50)
        for approval in approvals:
            if approval.tool_call_id == call_id:
                return ApprovalStatus(approval.status)
        return None

    async def _request_approval(
        self,
        run: AgentRun,
        call: LLMToolCall,
        permission: PermissionLevel,
        *,
        reason: str,
        goal: str,
    ) -> Approval:
        return await self._approvals.request(
            tool_name=call.name,
            tool_arguments=call.arguments,
            permission=permission,
            reason=reason,
            goal=goal,
            run_id=run.id,
            task_id=run.task_id,
            project_id=run.project_id,
            requested_by=run.user_id,
            tool_call_id=call.id,
        )

    async def _record_execution(
        self,
        run: AgentRun,
        call: LLMToolCall,
        permission: PermissionLevel,
        status: ToolExecutionStatus,
        *,
        result: ToolResult | None = None,
        verification: ToolVerification | None = None,
        error: str | None = None,
    ) -> ToolExecution:
        """Persist the audit row for one invocation (arguments are redacted)."""
        execution = ToolExecution(
            tool_name=call.name,
            status=status,
            permission=permission,
            arguments=redact(call.arguments),
            output=result.to_dict() if result is not None else None,
            error=error or (result.error if result is not None else None),
            duration_ms=result.duration_ms if result is not None else 0,
            verification_status=verification.status if verification else None,
            verification_reason=verification.reason if verification else None,
            agent_run_id=run.id,
            task_id=run.task_id,
            started_at=utcnow(),
        )
        self._session.add(execution)
        await self._session.flush()
        return execution

    @staticmethod
    def _error_outcome(
        call: LLMToolCall, message: str, *, permission: PermissionLevel | None
    ) -> ToolPhaseResult:
        return ToolPhaseResult(
            result_blocks=[_result_block(call.id, f"ERROR: {message}", True)],
            invocations=[
                ToolInvocation(
                    call_id=call.id,
                    tool_name=call.name,
                    arguments=call.arguments,
                    ok=False,
                    summary=truncate(message, 500),
                )
            ],
        )


def _result_block(call_id: str, content: str, is_error: bool) -> ContentBlock:
    """Build the ``tool_result`` block the provider expects."""
    return {
        "type": "tool_result",
        "tool_use_id": call_id,
        "content": truncate(content, _MAX_TOOL_RESULT_CHARS),
        "is_error": is_error,
    }


def _stringify(output: Any) -> str:
    """Render a tool's output for the model."""
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return str(output)
