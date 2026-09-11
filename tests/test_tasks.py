"""Task lifecycle and plan bookkeeping."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.core.enums import StepStatus, TaskStatus
from ulugbek_ai.core.errors import ConflictError
from ulugbek_ai.tasks.manager import TaskManager
from ulugbek_ai.tasks.schemas import Plan, PlanStep, TaskCreate


async def _task(session: AsyncSession):
    return await TaskManager(session).create(TaskCreate(goal="Deploy the ERP"))


async def test_new_task_starts_pending(session: AsyncSession) -> None:
    task = await _task(session)
    assert task.status is TaskStatus.PENDING
    assert task.started_at is None


async def test_the_happy_path(session: AsyncSession) -> None:
    manager = TaskManager(session)
    task = await _task(session)

    await manager.start_planning(task)
    await manager.start_running(task)
    assert task.started_at is not None

    await manager.complete(task, "Deployed.")
    assert task.status is TaskStatus.COMPLETED
    assert task.result == "Deployed."
    assert task.finished_at is not None


async def test_approval_detour(session: AsyncSession) -> None:
    manager = TaskManager(session)
    task = await _task(session)

    await manager.start_running(task)
    await manager.await_approval(task)
    assert task.status is TaskStatus.WAITING_APPROVAL

    await manager.transition(task, TaskStatus.RUNNING)
    await manager.complete(task, "done")
    assert task.status is TaskStatus.COMPLETED


async def test_illegal_transitions_are_rejected(session: AsyncSession) -> None:
    manager = TaskManager(session)
    task = await _task(session)
    await manager.start_running(task)
    await manager.complete(task, "done")

    with pytest.raises(ConflictError):
        await manager.transition(task, TaskStatus.RUNNING)
    with pytest.raises(ConflictError):
        await manager.fail(task, "too late")


async def test_failure_message_is_redacted(session: AsyncSession) -> None:
    manager = TaskManager(session)
    task = await _task(session)
    await manager.fail(task, "auth failed with api_key=sk-ant-0123456789abcdef")

    assert "sk-ant" not in task.error
    assert "REDACTED" in task.error


async def test_plan_attachment_and_step_progress(session: AsyncSession) -> None:
    manager = TaskManager(session)
    task = await _task(session)
    plan = Plan(
        goal_restatement="Deploy it",
        steps=[
            PlanStep(index=0, description="Check config", expected_outcome="valid"),
            PlanStep(index=1, description="Deploy", expected_outcome="live"),
        ],
    )

    await manager.attach_plan(task, plan)
    assert len(task.steps) == 2
    assert task.current_step == 0
    assert task.extra["plan_revision"] == 1

    assert manager.next_pending_step(task).index == 0

    await manager.mark_step(task, 0, StepStatus.DONE)
    assert task.current_step == 1
    assert manager.next_pending_step(task).index == 1

    await manager.mark_step(task, 1, StepStatus.DONE)
    assert manager.next_pending_step(task) is None


async def test_replanning_bumps_the_revision(session: AsyncSession) -> None:
    manager = TaskManager(session)
    task = await _task(session)

    await manager.attach_plan(task, Plan(steps=[PlanStep(index=0, description="a")]))
    await manager.mark_step(task, 0, StepStatus.FAILED)
    await manager.attach_plan(task, Plan(steps=[PlanStep(index=0, description="b")]))

    assert task.extra["plan_revision"] == 2
    assert task.current_step == 0
    assert manager.load_plan(task)[0].description == "b"


async def test_cancel(session: AsyncSession) -> None:
    manager = TaskManager(session)
    task = await _task(session)

    cancelled = await manager.cancel(task.id, reason="operator changed their mind")

    assert cancelled.status is TaskStatus.CANCELLED
    assert cancelled.finished_at is not None
