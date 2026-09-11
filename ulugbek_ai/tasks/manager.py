"""Task lifecycle.

State changes go through :meth:`TaskManager.transition`, which rejects illegal
moves. That keeps "how a task may progress" in one readable table instead of
scattered across the agent loop.
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import StepStatus, TaskStatus
from ulugbek_ai.core.errors import ConflictError, NotFoundError
from ulugbek_ai.core.redaction import redact_text
from ulugbek_ai.core.utils import utcnow
from ulugbek_ai.tasks.models import Task
from ulugbek_ai.tasks.repository import TaskRepository
from ulugbek_ai.tasks.schemas import Plan, PlanStep, TaskCreate

#: Legal status transitions. Terminal statuses have no outgoing edges.
ALLOWED_TRANSITIONS: Final[dict[TaskStatus, frozenset[TaskStatus]]] = {
    TaskStatus.PENDING: frozenset(
        {
            TaskStatus.PLANNING,
            TaskStatus.RUNNING,
            TaskStatus.CANCELLED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.PLANNING: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.PLANNING,  # replan
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.WAITING_APPROVAL: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


class TaskManager:
    """Creates tasks and moves them through their lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = TaskRepository(session)

    # ------------------------------------------------------------------ CRUD #
    async def create(self, payload: TaskCreate) -> Task:
        task = Task(
            goal=payload.goal,
            priority=payload.priority,
            project_id=payload.project_id,
            user_id=payload.user_id,
            parent_task_id=payload.parent_task_id,
            extra=payload.extra,
            status=TaskStatus.PENDING,
        )
        self._repository.add(task)
        await self._repository.flush()
        return task

    async def get(self, task_id: uuid.UUID) -> Task:
        task = await self._repository.get(task_id)
        if task is None:
            raise NotFoundError(
                f"Task {task_id} not found.", details={"task_id": str(task_id)}
            )
        return task

    async def list(self, **kwargs) -> list[Task]:
        return await self._repository.list(**kwargs)

    # ------------------------------------------------------------ transitions #
    async def transition(
        self,
        task: Task,
        status: TaskStatus,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> Task:
        """Move *task* to *status*, rejecting illegal transitions."""
        current = TaskStatus(task.status)
        if status == current:
            return task
        if status not in ALLOWED_TRANSITIONS[current]:
            raise ConflictError(
                f"Illegal task transition {current} -> {status}.",
                details={"task_id": str(task.id), "from": current, "to": status},
            )

        task.status = status
        if status is TaskStatus.RUNNING and task.started_at is None:
            task.started_at = utcnow()
        if result is not None:
            task.result = result
        if error is not None:
            task.error = redact_text(error)
        if status.is_terminal:
            task.finished_at = utcnow()

        await self._repository.flush()
        return task

    async def start_planning(self, task: Task) -> Task:
        return await self.transition(task, TaskStatus.PLANNING)

    async def start_running(self, task: Task) -> Task:
        return await self.transition(task, TaskStatus.RUNNING)

    async def await_approval(self, task: Task) -> Task:
        return await self.transition(task, TaskStatus.WAITING_APPROVAL)

    async def complete(self, task: Task, result: str) -> Task:
        return await self.transition(task, TaskStatus.COMPLETED, result=result)

    async def fail(self, task: Task, error: str) -> Task:
        return await self.transition(task, TaskStatus.FAILED, error=error)

    async def cancel(self, task_id: uuid.UUID, *, reason: str | None = None) -> Task:
        task = await self.get(task_id)
        return await self.transition(task, TaskStatus.CANCELLED, error=reason)

    # ------------------------------------------------------------------ plan #
    async def attach_plan(self, task: Task, plan: Plan) -> Task:
        """Store a plan on the task and reset progress to its first step."""
        task.steps = [step.model_dump(mode="json") for step in plan.steps]
        task.current_step = 0
        extra = dict(task.extra or {})
        extra["goal_restatement"] = plan.goal_restatement
        extra["plan_revision"] = int(extra.get("plan_revision", 0)) + 1
        task.extra = extra
        await self._repository.flush()
        return task

    def load_plan(self, task: Task) -> list[PlanStep]:
        """Deserialize the stored plan."""
        return [PlanStep.model_validate(step) for step in task.steps or []]

    async def mark_step(
        self,
        task: Task,
        index: int,
        status: StepStatus,
        *,
        notes: str | None = None,
    ) -> Task:
        """Update one step's status and advance ``current_step`` when it ends."""
        steps = self.load_plan(task)
        if not 0 <= index < len(steps):
            raise NotFoundError(
                f"Step {index} does not exist on task {task.id}.",
                details={"task_id": str(task.id), "index": index},
            )
        steps[index] = steps[index].mark(status, notes=notes)
        task.steps = [step.model_dump(mode="json") for step in steps]
        if status in (StepStatus.DONE, StepStatus.SKIPPED):
            task.current_step = min(index + 1, len(steps))
        await self._repository.flush()
        return task

    def next_pending_step(self, task: Task) -> PlanStep | None:
        """First step that still needs work, or ``None`` when the plan is done."""
        for step in self.load_plan(task):
            if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                return step
        return None
