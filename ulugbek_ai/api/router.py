"""Aggregate router.

Authentication is applied here, once, rather than route by route. Two reasons,
both learned from what this file used to look like:

* A guard listed on each route is a guard that can be left off one, and it was:
  of thirty-three endpoints, twenty-one never mentioned the seam at all —
  every read, the whole event feed, and the dashboard's overview among them.
* A dependency in an endpoint's own signature is resolved *alongside* its
  siblings, so an unauthenticated caller could reach the LLM dependency and be
  told which credential the server is missing. Router-level dependencies run
  first, so nothing else is touched until the caller is known.

Anything added below inherits the guard by default. Only health is exempt, and
it is exempt deliberately: a platform has to be able to ask whether the service
is alive without holding a credential.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ulugbek_ai.api.deps import require_principal
from ulugbek_ai.api.routes import (
    agent,
    approvals,
    events,
    health,
    memory,
    projects,
    system,
    tasks,
    tools,
    voice,
)

#: Every protected router is included with this.
_OPERATOR_ONLY = [Depends(require_principal)]

api_router = APIRouter()

# Open: liveness must not need a credential. It reports whether things are
# configured, never what they are configured to.
api_router.include_router(health.router)

for _protected in (
    agent.router,
    projects.router,
    tasks.router,
    memory.router,
    approvals.router,
    events.router,
    tools.router,
    system.router,
    voice.router,
):
    api_router.include_router(_protected, dependencies=_OPERATOR_ONLY)

# The one exception, and the reason it is an exception is written down next to
# it: EventSource sends no headers, so the live stream proves itself with a
# signed, short-lived token instead of the operator's own.
api_router.include_router(events.stream_router)

__all__ = ["api_router"]
