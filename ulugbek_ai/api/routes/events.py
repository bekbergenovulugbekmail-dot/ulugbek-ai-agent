"""Event feed and live stream.

Two transports over the same projection:

* ``GET /events`` and ``GET /events/runs/{id}`` — polling, which is what a
  simple client starts with.
* ``GET /events/runs/{id}/stream`` — Server-Sent Events, for a live timeline.

The stream is deliberately a thin loop over the same repository query rather
than an in-process pub/sub: it therefore works across worker processes and
survives a restart, and a client reconnecting just passes the last sequence it
saw. Swapping in a push-based fan-out later changes this module only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ulugbek_ai.agent.repository import AgentRunRepository
from ulugbek_ai.api.deps import DatabaseDep, EventServiceDep
from ulugbek_ai.core.enums import RunStatus, StepType
from ulugbek_ai.events.projector import project_steps
from ulugbek_ai.events.repository import EventRepository
from ulugbek_ai.events.schemas import AgentStateSnapshot, EventPage
from ulugbek_ai.events.service import EventService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

#: How often the stream looks for new events.
STREAM_POLL_SECONDS = 0.75
#: Comment frames keep proxies from closing an idle connection.
STREAM_HEARTBEAT_SECONDS = 15.0
#: A stream on a finished run ends rather than polling forever.
STREAM_MAX_SECONDS = 900.0


@router.get("", response_model=EventPage, summary="Global activity feed")
async def list_events(
    events: EventServiceDep,
    run_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    type_filter: list[StepType] | None = Query(default=None, alias="type"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> EventPage:
    """Newest-first activity across every run."""
    return await events.recent(
        types=type_filter,
        run_id=run_id,
        task_id=task_id,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/runs/{run_id}",
    response_model=EventPage,
    summary="Ordered events of one run",
)
async def list_run_events(
    run_id: uuid.UUID,
    events: EventServiceDep,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
) -> EventPage:
    """Oldest-first events of a run; poll with the returned ``cursor``."""
    return await events.for_run(
        run_id, after_sequence=after_sequence, limit=limit
    )


@router.get("/state", response_model=AgentStateSnapshot, summary="Agent state")
async def agent_state(
    events: EventServiceDep, run_id: uuid.UUID | None = None
) -> AgentStateSnapshot:
    """The agent's current phase, for the headline status indicator."""
    return await events.state(run_id)


def _frame(event_name: str, payload: object) -> str:
    """One SSE frame."""
    body = json.dumps(payload, default=str)
    return f"event: {event_name}\ndata: {body}\n\n"


async def _stream_run_events(
    database: DatabaseDep,
    run_id: uuid.UUID,
    after_sequence: int,
    request: Request,
) -> AsyncIterator[str]:
    """Yield SSE frames until the run settles or the client goes away."""
    cursor = after_sequence
    elapsed = 0.0
    since_heartbeat = 0.0
    settled_status: RunStatus | None = None

    while elapsed < STREAM_MAX_SECONDS:
        if await request.is_disconnected():
            return

        # A short-lived session per poll: a streaming response must not hold a
        # transaction open for minutes.
        async with database.session() as session:
            steps = await EventRepository(session).list_for_run(
                run_id, after_sequence=cursor, limit=100
            )
            run = await AgentRunRepository(session).get(run_id)
            state = (
                await EventService(session).state(run_id)
                if run is not None
                else None
            )

        if run is None or state is None:
            yield _frame("error", {"message": "Run not found."})
            return

        for event in project_steps(steps, project_id=run.project_id):
            cursor = event.sequence
            yield _frame("agent-event", event.model_dump(mode="json"))
            since_heartbeat = 0.0

        if steps:
            yield _frame("agent-state", state.model_dump(mode="json"))

        status = RunStatus(run.status)
        if status is not RunStatus.RUNNING:
            # Emit the terminal state once, then close the stream cleanly.
            if settled_status is None:
                settled_status = status
                yield _frame("agent-state", state.model_dump(mode="json"))
                yield _frame("done", {"run_id": str(run_id), "status": status.value})
                return

        await asyncio.sleep(STREAM_POLL_SECONDS)
        elapsed += STREAM_POLL_SECONDS
        since_heartbeat += STREAM_POLL_SECONDS

        if since_heartbeat >= STREAM_HEARTBEAT_SECONDS:
            since_heartbeat = 0.0
            yield ": heartbeat\n\n"

    yield _frame("timeout", {"run_id": str(run_id)})


@router.get(
    "/runs/{run_id}/stream",
    summary="Live event stream for one run (SSE)",
    response_class=StreamingResponse,
)
async def stream_run_events(
    run_id: uuid.UUID,
    request: Request,
    database: DatabaseDep,
    after_sequence: int = Query(default=0, ge=0),
) -> StreamingResponse:
    """Server-Sent Events for a run.

    Frames: ``agent-event`` (one timeline entry), ``agent-state`` (phase
    changed), ``done`` (the run settled), ``error``, ``timeout``.
    """
    return StreamingResponse(
        _stream_run_events(database, run_id, after_sequence, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
