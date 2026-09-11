"""The agent loop.

    UNDERSTAND -> LOAD CONTEXT -> PLAN -> SELECT TOOL -> EXECUTE -> OBSERVE
              -> REASON -> VERIFY -> COMPLETE
                                  \\-> REPLAN -> EXECUTE -> VERIFY

Three properties the loop guarantees:

* **It terminates.** Iterations are capped, replans are capped, a wall-clock
  deadline is checked between iterations, every tool call runs under its own
  timeout and the LLM client carries a request timeout. There is no path that
  spins forever.
* **It is resumable.** The transcript, the observations and any half-finished
  tool phase live on the ``agent_runs`` row, so a run paused for an approval
  resumes exactly where it stopped — in a different process if need be.
* **It does not lie.** A final answer is verified before the task is marked
  COMPLETED; a failed verification triggers a replan rather than a confident
  wrong answer.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.agent.context import ContextBuilder
from ulugbek_ai.agent.executor import Executor
from ulugbek_ai.agent.models import AgentRun
from ulugbek_ai.agent.planner import Planner
from ulugbek_ai.agent.prompts import EXECUTOR_SYSTEM, context_block
from ulugbek_ai.agent.repository import AgentRunRepository
from ulugbek_ai.agent.schemas import (
    AgentRunRequest,
    AgentRunResponse,
    ApprovalRequestInfo,
    VerificationInfo,
)
from ulugbek_ai.agent.verifier import Observation, VerificationResult, Verifier
from ulugbek_ai.approvals.manager import ApprovalManager
from ulugbek_ai.approvals.models import Approval
from ulugbek_ai.config.settings import Settings, get_settings
from ulugbek_ai.core.enums import (
    MemoryType,
    RunStatus,
    StepStatus,
    StepType,
    TaskStatus,
)
from ulugbek_ai.core.errors import ConflictError, LLMError, NotFoundError
from ulugbek_ai.core.redaction import redact_text, truncate
from ulugbek_ai.core.utils import utcnow
from ulugbek_ai.identity.repository import UserRepository
from ulugbek_ai.llm.base import LLMClient, LLMMessage, LLMToolCall, LLMUsage
from ulugbek_ai.memory.manager import MemoryManager
from ulugbek_ai.observability.audit import AuditLogger
from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.models import Project
from ulugbek_ai.tasks.manager import TaskManager
from ulugbek_ai.tasks.models import Task
from ulugbek_ai.tasks.schemas import Plan, PlanStep, TaskCreate
from ulugbek_ai.tools.base import ToolContext
from ulugbek_ai.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

#: Key under ``AgentRun.extra`` holding the observation log.
OBSERVATIONS_KEY = "observations"
#: Key under ``AgentRun.extra`` holding the serialized plan.
PLAN_KEY = "plan"


@dataclass(slots=True)
class _Resolution:
    """The run's resolved subjects: who, which project, which task."""

    user_id: uuid.UUID | None
    project: Project | None
    task: Task


class AgentEngine:
    """Orchestrates one agent run from request to verified result."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        llm: LLMClient,
        registry: ToolRegistry,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._llm = llm
        self._registry = registry
        self._settings = settings or get_settings()

        self._runs = AgentRunRepository(session)
        self._tasks = TaskManager(session)
        self._projects = ProjectManager(session)
        self._users = UserRepository(session)
        self._memories = MemoryManager(session, llm=llm)
        self._approvals = ApprovalManager(session)
        self._planner = Planner(llm)
        self._verifier = Verifier(
            llm, enabled=self._settings.agent_verification_enabled
        )
        self._context_builder = ContextBuilder(
            session,
            memory_limit=self._settings.memory_context_limit,
            max_chars=self._settings.memory_context_max_chars,
        )

    # ==================================================================== run #
    async def run(self, request: AgentRunRequest) -> AgentRunResponse:
        """Execute a user request end to end."""
        resolution = await self._resolve(request)
        run = await self._create_run(request, resolution)
        audit = AuditLogger(
            self._session, run_id=run.id, task_id=resolution.task.id
        )

        try:
            plan, system_prompt = await self._prepare(
                request, resolution, run, audit
            )
        except LLMError as exc:
            return await self._fail(run, resolution.task, audit, exc.message)

        run.system_prompt = system_prompt
        run.transcript = [LLMMessage.user(request.message).to_dict()]
        await self._runs.flush()

        return await self._loop(
            run=run,
            task=resolution.task,
            audit=audit,
            plan=plan,
            max_iterations=request.max_iterations
            or self._settings.agent_max_iterations,
        )

    # ================================================================= resume #
    async def resume(self, run_id: uuid.UUID) -> AgentRunResponse:
        """Continue a run that was paused waiting for an approval."""
        run = await self._runs.get(run_id)
        if run is None:
            raise NotFoundError(
                f"Agent run {run_id} not found.", details={"run_id": str(run_id)}
            )
        if run.status is not RunStatus.WAITING_APPROVAL:
            raise ConflictError(
                f"Run {run_id} is {run.status}, not waiting for approval.",
                details={"run_id": str(run_id), "status": str(run.status)},
            )

        pending = await self._approvals.get_pending_for_run(run.id)
        if pending is not None:
            raise ConflictError(
                f"Run {run_id} still has an undecided approval.",
                details={
                    "run_id": str(run_id),
                    "approval_id": str(pending.id),
                },
            )

        task = await self._tasks.get(run.task_id) if run.task_id else None
        if task is None:
            raise ConflictError(
                f"Run {run_id} has no task to resume.",
                details={"run_id": str(run_id)},
            )

        audit = AuditLogger(self._session, run_id=run.id, task_id=task.id)
        audit.set_sequence(await self._runs.max_step_sequence(run.id))

        run.status = RunStatus.RUNNING
        if task.status is TaskStatus.WAITING_APPROVAL:
            await self._tasks.transition(task, TaskStatus.RUNNING)
        await self._runs.flush()

        plan = self._load_plan(run)
        return await self._loop(
            run=run,
            task=task,
            audit=audit,
            plan=plan,
            max_iterations=self._settings.agent_max_iterations,
        )

    # ============================================================== main loop #
    async def _loop(
        self,
        *,
        run: AgentRun,
        task: Task,
        audit: AuditLogger,
        plan: Plan | None,
        max_iterations: int,
    ) -> AgentRunResponse:
        transcript = [
            LLMMessage.from_dict(message) for message in run.transcript or []
        ]
        observations = self._load_observations(run)
        tools_used: list[str] = list((run.extra or {}).get("tools_used", []))
        usage = LLMUsage(**(run.token_usage or {}))
        deadline = time.monotonic() + self._settings.agent_run_timeout_seconds
        replans_left = self._settings.agent_max_replans - run.replans

        tool_context = ToolContext(
            session=self._session,
            run_id=run.id,
            task_id=task.id,
            project_id=run.project_id,
            user_id=run.user_id,
        )
        executor = Executor(
            self._session,
            registry=self._registry,
            approvals=self._approvals,
            audit=audit,
        )

        # A resumed run re-enters mid tool-phase: finish it before asking again.
        resumed_phase = await self._resume_tool_phase(
            run=run,
            task=task,
            transcript=transcript,
            executor=executor,
            tool_context=tool_context,
            observations=observations,
            tools_used=tools_used,
            audit=audit,
        )
        if resumed_phase is not None:
            return resumed_phase

        iterations_left = max_iterations
        while iterations_left > 0:
            if time.monotonic() > deadline:
                return await self._fail(
                    run,
                    task,
                    audit,
                    f"Run exceeded its {self._settings.agent_run_timeout_seconds:.0f}s "
                    "time budget.",
                    usage=usage,
                    tools_used=tools_used,
                )

            iterations_left -= 1
            run.iterations += 1
            iteration = run.iterations

            try:
                response = await self._llm.complete(
                    transcript,
                    system=run.system_prompt,
                    tools=self._registry.llm_specs(),
                )
            except LLMError as exc:
                return await self._fail(
                    run, task, audit, exc.message, usage=usage, tools_used=tools_used
                )

            usage = usage + response.usage
            run.model = response.model or self._llm.model
            await audit.llm_call(
                f"Model replied ({response.stop_reason})",
                {
                    "stop_reason": response.stop_reason,
                    "tool_calls": [call.name for call in response.tool_calls],
                    "usage": response.usage.to_dict(),
                },
                iteration,
            )

            transcript.append(response.as_message())
            self._persist(run, transcript, observations, tools_used, usage)

            # --- SELECT TOOL / EXECUTE / OBSERVE ---------------------------- #
            if response.has_tool_calls:
                phase = await executor.resolve_tool_calls(
                    run,
                    response.tool_calls,
                    tool_context,
                    iteration=iteration,
                    goal=task.goal,
                )
                for invocation in phase.invocations:
                    if invocation.replayed:
                        continue
                    observations.append(
                        Observation(
                            tool_name=invocation.tool_name,
                            ok=invocation.ok,
                            summary=invocation.summary,
                        )
                    )
                    if invocation.tool_name not in tools_used:
                        tools_used.append(invocation.tool_name)

                if phase.paused:
                    self._persist(run, transcript, observations, tools_used, usage)
                    return await self._pause_for_approval(
                        run, task, audit, phase.pending_approval, usage, tools_used
                    )

                transcript.append(LLMMessage.tool_results(phase.result_blocks))
                self._persist(run, transcript, observations, tools_used, usage)
                await self._advance_plan(task, plan, succeeded=all(
                    invocation.ok for invocation in phase.invocations
                ))
                continue

            # --- REASON / VERIFY -------------------------------------------- #
            answer = response.text.strip()
            if not answer:
                observations.append(
                    Observation("model", False, "empty reply with no tool call")
                )
                transcript.append(
                    LLMMessage.user(
                        "Your reply was empty. Either call a tool or give the "
                        "final answer as plain text."
                    )
                )
                continue

            verification = await self._verifier.verify(
                goal=task.goal,
                answer=answer,
                plan=plan,
                observations=observations,
            )
            await audit.verification(
                f"Verification: {verification.status}",
                verification.to_dict(),
                success=verification.is_success,
            )

            if not verification.is_failure:
                return await self._complete(
                    run, task, audit, answer, verification, usage, tools_used
                )

            # --- REPLAN ------------------------------------------------------ #
            if replans_left <= 0:
                return await self._fail(
                    run,
                    task,
                    audit,
                    f"Verification failed and no replans remain: {verification.reason}",
                    usage=usage,
                    tools_used=tools_used,
                )

            replans_left -= 1
            run.replans += 1
            plan = await self._replan(
                run, task, audit, plan, verification, observations
            )
            transcript.append(
                LLMMessage.user(
                    "Verification rejected that answer: "
                    f"{verification.reason}\n\n"
                    "Follow the corrected plan and try again:\n"
                    + "\n".join(
                        f"{step.index + 1}. {step.description}"
                        for step in (plan.steps if plan else [])
                    )
                )
            )
            self._persist(run, transcript, observations, tools_used, usage)

        return await self._fail(
            run,
            task,
            audit,
            f"Reached the maximum of {max_iterations} iterations without "
            "completing the goal.",
            usage=usage,
            tools_used=tools_used,
        )

    # ======================================================= loop sub-phases #
    async def _resume_tool_phase(
        self,
        *,
        run: AgentRun,
        task: Task,
        transcript: list[LLMMessage],
        executor: Executor,
        tool_context: ToolContext,
        observations: list[Observation],
        tools_used: list[str],
        audit: AuditLogger,
    ) -> AgentRunResponse | None:
        """Finish a tool phase that a pause interrupted.

        Returns a response only when the run pauses again; otherwise it appends
        the completed ``tool_result`` turn and lets the main loop continue.
        """
        if not transcript or transcript[-1].role != "assistant":
            return None

        pending_calls = [
            block for block in transcript[-1].content
            if block.get("type") == "tool_use"
        ]
        if not pending_calls:
            return None

        calls = [
            LLMToolCall(
                id=block["id"], name=block["name"], arguments=dict(block.get("input") or {})
            )
            for block in pending_calls
        ]
        phase = await executor.resolve_tool_calls(
            run, calls, tool_context, iteration=run.iterations, goal=task.goal
        )
        for invocation in phase.invocations:
            if invocation.replayed:
                continue
            observations.append(
                Observation(
                    tool_name=invocation.tool_name,
                    ok=invocation.ok,
                    summary=invocation.summary,
                )
            )
            if invocation.tool_name not in tools_used:
                tools_used.append(invocation.tool_name)

        usage = LLMUsage(**(run.token_usage or {}))
        if phase.paused:
            self._persist(run, transcript, observations, tools_used, usage)
            return await self._pause_for_approval(
                run, task, audit, phase.pending_approval, usage, tools_used
            )

        transcript.append(LLMMessage.tool_results(phase.result_blocks))
        self._persist(run, transcript, observations, tools_used, usage)
        return None

    async def _prepare(
        self,
        request: AgentRunRequest,
        resolution: _Resolution,
        run: AgentRun,
        audit: AuditLogger,
    ) -> tuple[Plan, str]:
        """UNDERSTAND + LOAD CONTEXT + PLAN."""
        context = await self._context_builder.build(
            request.message,
            project=resolution.project,
            user_id=resolution.user_id,
        )
        await audit.record(
            StepType.AGENT_RUN,
            summary="Context loaded",
            payload=context.summary(),
        )

        await self._tasks.start_planning(resolution.task)
        plan = await self._planner.plan(
            resolution.task.goal,
            tools=self._registry.list(),
            context=context.render(),
        )
        await self._tasks.attach_plan(resolution.task, plan)
        await audit.plan(
            f"Planned {len(plan.steps)} step(s)",
            {
                "goal_restatement": plan.goal_restatement,
                "requires_tools": plan.requires_tools,
                "steps": [step.model_dump(mode="json") for step in plan.steps],
            },
        )
        self._store_plan(run, plan)
        await self._tasks.start_running(resolution.task)

        system_prompt = "\n\n".join(
            part
            for part in (
                EXECUTOR_SYSTEM,
                context.render(),
                context_block({"plan": _render_plan(plan)}),
            )
            if part.strip()
        )
        return plan, system_prompt

    async def _replan(
        self,
        run: AgentRun,
        task: Task,
        audit: AuditLogger,
        plan: Plan | None,
        verification: VerificationResult,
        observations: list[Observation],
    ) -> Plan:
        await self._tasks.transition(task, TaskStatus.PLANNING)
        new_plan = await self._planner.replan(
            task.goal,
            tools=self._registry.list(),
            previous_plan=plan or Plan(),
            failure_reason=verification.reason,
            observations=[
                f"{item.tool_name}: {'ok' if item.ok else 'failed'} — {item.summary}"
                for item in observations
            ],
        )
        await self._tasks.attach_plan(task, new_plan)
        await self._tasks.start_running(task)
        await audit.plan(
            f"Replanned into {len(new_plan.steps)} step(s)",
            {
                "reason": verification.reason,
                "steps": [step.model_dump(mode="json") for step in new_plan.steps],
            },
            replan=True,
        )
        self._store_plan(run, new_plan)
        return new_plan

    async def _advance_plan(
        self, task: Task, plan: Plan | None, *, succeeded: bool
    ) -> None:
        """Mark the current plan step done (or failed) after a tool phase."""
        if plan is None or not plan.steps:
            return
        step: PlanStep | None = self._tasks.next_pending_step(task)
        if step is None:
            return
        await self._tasks.mark_step(
            task,
            step.index,
            StepStatus.DONE if succeeded else StepStatus.FAILED,
        )

    # ================================================================ outcomes #
    async def _complete(
        self,
        run: AgentRun,
        task: Task,
        audit: AuditLogger,
        answer: str,
        verification: VerificationResult,
        usage: LLMUsage,
        tools_used: list[str],
    ) -> AgentRunResponse:
        run.status = RunStatus.COMPLETED
        run.output = answer
        run.finished_at = utcnow()
        run.token_usage = usage.to_dict()
        await self._tasks.complete(task, answer)
        await audit.final_result(
            "Run completed",
            {"answer": truncate(answer, 2_000), "verification": verification.to_dict()},
            success=True,
        )
        await self._remember_outcome(run, task, answer)
        await self._runs.flush()

        return self._response(
            run,
            task,
            verification=verification,
            tools_used=tools_used,
            usage=usage,
        )

    async def _fail(
        self,
        run: AgentRun,
        task: Task | None,
        audit: AuditLogger,
        error: str,
        *,
        usage: LLMUsage | None = None,
        tools_used: list[str] | None = None,
    ) -> AgentRunResponse:
        run.status = RunStatus.FAILED
        run.error = redact_text(error)
        run.finished_at = utcnow()
        if usage is not None:
            run.token_usage = usage.to_dict()
        if task is not None and not TaskStatus(task.status).is_terminal:
            await self._tasks.fail(task, error)
        await audit.error("Run failed", {"error": redact_text(error)})
        await self._runs.flush()

        return self._response(
            run, task, tools_used=tools_used or [], usage=usage or LLMUsage()
        )

    async def _pause_for_approval(
        self,
        run: AgentRun,
        task: Task,
        audit: AuditLogger,
        approval: Approval,
        usage: LLMUsage,
        tools_used: list[str],
    ) -> AgentRunResponse:
        run.status = RunStatus.WAITING_APPROVAL
        run.token_usage = usage.to_dict()
        if task.status is not TaskStatus.WAITING_APPROVAL:
            await self._tasks.await_approval(task)
        await audit.record(
            StepType.APPROVAL,
            summary=f"Run paused: {approval.tool_name} needs approval",
            payload={"approval_id": str(approval.id), "tool": approval.tool_name},
        )
        await self._runs.flush()

        return self._response(
            run,
            task,
            tools_used=tools_used,
            usage=usage,
            approval=ApprovalRequestInfo(
                approval_id=approval.id,
                tool_name=approval.tool_name,
                permission=str(approval.permission),
                reason=approval.reason,
                tool_arguments=approval.tool_arguments,
            ),
        )

    # ================================================================ helpers #
    async def _resolve(self, request: AgentRunRequest) -> _Resolution:
        """Identify the user, the project and the task for this request."""
        if request.user_id is not None:
            user_id = request.user_id
        else:
            user = await self._users.get_or_create_default()
            user_id = user.id

        match = await self._projects.resolve(
            request.message, project_id=request.project_id
        )
        project = match.project if match else None

        if request.task_id is not None:
            task = await self._tasks.get(request.task_id)
            if TaskStatus(task.status).is_terminal:
                raise ConflictError(
                    f"Task {task.id} is already {task.status}.",
                    details={"task_id": str(task.id)},
                )
        else:
            task = await self._tasks.create(
                TaskCreate(
                    goal=request.message,
                    project_id=project.id if project else None,
                    user_id=user_id,
                    extra={"routing": match.reason if match else "unrouted"},
                )
            )

        return _Resolution(user_id=user_id, project=project, task=task)

    async def _create_run(
        self, request: AgentRunRequest, resolution: _Resolution
    ) -> AgentRun:
        run = AgentRun(
            input=request.message,
            status=RunStatus.RUNNING,
            task_id=resolution.task.id,
            project_id=resolution.project.id if resolution.project else None,
            user_id=resolution.user_id,
            model=self._llm.model,
            started_at=utcnow(),
            extra={"metadata": request.metadata} if request.metadata else {},
        )
        self._runs.add(run)
        await self._runs.flush()
        return run

    def _persist(
        self,
        run: AgentRun,
        transcript: list[LLMMessage],
        observations: list[Observation],
        tools_used: list[str],
        usage: LLMUsage,
    ) -> None:
        """Mirror in-memory loop state onto the run row (flushed by the caller)."""
        run.transcript = [message.to_dict() for message in transcript]
        run.token_usage = usage.to_dict()
        extra = dict(run.extra or {})
        extra[OBSERVATIONS_KEY] = [
            {"tool": item.tool_name, "ok": item.ok, "summary": item.summary}
            for item in observations
        ]
        extra["tools_used"] = tools_used
        run.extra = extra

    @staticmethod
    def _load_observations(run: AgentRun) -> list[Observation]:
        return [
            Observation(
                tool_name=item.get("tool", "unknown"),
                ok=bool(item.get("ok")),
                summary=str(item.get("summary", "")),
            )
            for item in (run.extra or {}).get(OBSERVATIONS_KEY, [])
        ]

    @staticmethod
    def _store_plan(run: AgentRun, plan: Plan) -> None:
        extra = dict(run.extra or {})
        extra[PLAN_KEY] = plan.model_dump(mode="json")
        run.extra = extra

    @staticmethod
    def _load_plan(run: AgentRun) -> Plan | None:
        payload = (run.extra or {}).get(PLAN_KEY)
        return Plan.model_validate(payload) if payload else None

    async def _remember_outcome(
        self, run: AgentRun, task: Task, answer: str
    ) -> None:
        """Persist a compact record of what was achieved, for future runs."""
        try:
            await self._memories.remember(
                f"Goal: {truncate(task.goal, 500)}\nOutcome: {truncate(answer, 1_500)}",
                type=MemoryType.TASK_CONTEXT,
                user_id=run.user_id,
                project_id=run.project_id,
                task_id=task.id,
                importance=0.4,
                source="agent.run",
            )
        except Exception:  # noqa: BLE001 - memory write must not fail a good run
            logger.exception("Could not store the run outcome in memory")

    def _response(
        self,
        run: AgentRun,
        task: Task | None,
        *,
        verification: VerificationResult | None = None,
        approval: ApprovalRequestInfo | None = None,
        tools_used: list[str],
        usage: LLMUsage,
    ) -> AgentRunResponse:
        return AgentRunResponse(
            run_id=run.id,
            task_id=task.id if task else None,
            project_id=run.project_id,
            status=RunStatus(run.status),
            task_status=TaskStatus(task.status) if task else None,
            output=run.output,
            error=run.error,
            iterations=run.iterations,
            replans=run.replans,
            tools_used=tools_used,
            verification=(
                VerificationInfo(
                    status=verification.status, reason=verification.reason
                )
                if verification
                else None
            ),
            approval=approval,
            token_usage=usage.to_dict(),
        )


def _render_plan(plan: Plan) -> str:
    if not plan.steps:
        return "(no plan)"
    lines = [plan.goal_restatement] if plan.goal_restatement else []
    lines.extend(
        f"{step.index + 1}. {step.description}"
        + (f" [tool: {step.tool}]" if step.tool else "")
        + (f" -> expect: {step.expected_outcome}" if step.expected_outcome else "")
        for step in plan.steps
    )
    return "\n".join(lines)
