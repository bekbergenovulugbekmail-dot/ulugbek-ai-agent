"""Background execution of agent runs.

``POST /api/agent/run`` blocks until the agent settles, which is right for a
script but wrong for a control centre: an operator wants the run id immediately
and the activity streamed as it happens.

The runner closes that gap. It owns no request state — each background run opens
its own session from the :class:`~ulugbek_ai.database.session.Database`, so it is
unaffected by the HTTP request that launched it ending.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from ulugbek_ai.agent.engine import AgentEngine
from ulugbek_ai.agent.repository import AgentRunRepository
from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.enums import RunStatus, TaskStatus
from ulugbek_ai.core.errors import UlugbekError
from ulugbek_ai.core.redaction import redact_text
from ulugbek_ai.core.utils import utcnow
from ulugbek_ai.database.session import Database
from ulugbek_ai.llm.base import LLMClient
from ulugbek_ai.tasks.manager import TaskManager
from ulugbek_ai.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class BackgroundAgentRunner:
    """Launches agent runs as fire-and-forget asyncio tasks."""

    def __init__(
        self,
        database: Database,
        *,
        llm: LLMClient,
        registry: ToolRegistry,
        settings: Settings,
    ) -> None:
        self._database = database
        self._llm = llm
        self._registry = registry
        self._settings = settings
        #: Strong references, so a running task is never garbage collected.
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    def launch(self, run_id: uuid.UUID, *, resume: bool = False) -> None:
        """Start executing *run_id* in the background."""
        task = asyncio.create_task(self._execute(run_id, resume=resume))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _execute(self, run_id: uuid.UUID, *, resume: bool) -> None:
        try:
            async with self._database.session() as session:
                engine = AgentEngine(
                    session,
                    llm=self._llm,
                    registry=self._registry,
                    settings=self._settings,
                )
                if resume:
                    await engine.resume(run_id)
                else:
                    await engine.execute(run_id)
        except UlugbekError as exc:
            logger.warning(
                "Background run %s stopped: %s", run_id, exc.message
            )
            await self._mark_failed(run_id, exc.message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a run must never take the app down
            logger.exception("Background run %s crashed", run_id)
            await self._mark_failed(run_id, f"{type(exc).__name__}: {exc}")

    async def _mark_failed(self, run_id: uuid.UUID, error: str) -> None:
        """Record the failure so the run never sits in RUNNING forever."""
        try:
            async with self._database.session() as session:
                runs = AgentRunRepository(session)
                run = await runs.get(run_id)
                if run is None or RunStatus(run.status) is not RunStatus.RUNNING:
                    return
                run.status = RunStatus.FAILED
                run.error = redact_text(error)
                run.finished_at = utcnow()
                if run.task_id is not None:
                    tasks = TaskManager(session)
                    task = await tasks.get(run.task_id)
                    if not TaskStatus(task.status).is_terminal:
                        await tasks.fail(task, error)
                await runs.flush()
        except Exception:  # noqa: BLE001 - best effort bookkeeping
            logger.exception("Could not mark run %s as failed", run_id)

    async def drain(self, timeout: float = 10.0) -> None:
        """Wait for in-flight runs on shutdown, then give up rather than hang."""
        if not self._tasks:
            return
        done, pending = await asyncio.wait(set(self._tasks), timeout=timeout)
        for task in pending:
            task.cancel()
        logger.info(
            "Runner drained: %d finished, %d cancelled", len(done), len(pending)
        )
