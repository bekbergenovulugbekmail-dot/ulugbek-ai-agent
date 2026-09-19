"""Railway tools.

The read tools let the agent answer what is actually running: which services a
project has, whether the last deployment succeeded, and what the build said
when it did not. The one write tool deploys, and it is classified
``CRITICAL`` — the only level the permission policy will not let a
configuration make automatic. A deploy reaches real users, so it stops and asks
a human every single time, and the answer to "did it work?" comes from
Railway's own deployment status rather than from the fact that the mutation
returned without raising.

Addressing a service on Railway needs three ids (project, environment,
service). They resolve the same way for every tool: an explicit argument wins,
then the current project's Railway binding, then the configured defaults. The
error names the one that is missing and how to find it, because a bare "invalid
input" is useless to whoever has to fix it.
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from ulugbek_ai.core.enums import PermissionLevel
from ulugbek_ai.core.errors import ValidationError
from ulugbek_ai.integrations.railway.client import (
    DEFAULT_API_URL,
    RailwayApiError,
    RailwayClient,
    TokenKind,
    classify_status,
)
from ulugbek_ai.projects.repository import ProjectRepository
from ulugbek_ai.tools.base import Tool, ToolContext, ToolResult, ToolVerification

logger = logging.getLogger(__name__)

SERVICE = "railway"

#: How long a deploy waits for Railway to reach a terminal status before
#: reporting what it saw. A build that outlasts this is reported as still
#: running — never as a success.
DEFAULT_WAIT_SECONDS: float = 90.0
#: Gap between status polls while waiting for a deployment.
POLL_SECONDS: float = 5.0
#: Headroom between the wait budget and the tool timeout the registry enforces,
#: so a deploy reports its finding instead of being killed mid-poll.
TIMEOUT_MARGIN_SECONDS: float = 30.0

_ID_PROPERTIES: dict[str, Any] = {
    "project_id": {
        "type": "string",
        "description": (
            "Railway project id. Omit it to use the project configured for "
            "this work."
        ),
    },
    "environment_id": {
        "type": "string",
        "description": "Railway environment id (production, staging, ...).",
    },
    "service_id": {
        "type": "string",
        "description": "Railway service id — the thing that gets deployed.",
    },
}


@dataclass(slots=True)
class RailwayTarget:
    """The three ids every Railway operation is addressed by."""

    project_id: str
    environment_id: str
    service_id: str


class RailwayTool(Tool):
    """Shared behaviour for every Railway tool."""

    service = SERVICE
    timeout_seconds = 40.0

    def __init__(
        self,
        client: RailwayClient | None = None,
        *,
        defaults: dict[str, str | None] | None = None,
    ) -> None:
        self._client = client or RailwayClient()
        self._defaults = {k: v for k, v in (defaults or {}).items() if v}

    @property
    def client(self) -> RailwayClient:
        return self._client

    async def _binding(self, context: ToolContext) -> dict[str, Any]:
        """The current project's Railway binding, if there is one."""
        if context.session is None or context.project_id is None:
            return {}
        project = await ProjectRepository(context.session).get(context.project_id)
        if project is None:
            return {}
        binding = (project.integrations or {}).get(SERVICE)
        return binding if isinstance(binding, dict) else {}

    async def resolve_project_id(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> str:
        explicit = arguments.get("project_id")
        if explicit:
            return str(explicit)
        binding = await self._binding(context)
        candidate = binding.get("project_id") or self._defaults.get("project_id")
        if candidate:
            return str(candidate)
        raise ValidationError(
            "No Railway project id given, and none is configured. Call "
            "railway_list_projects to see the ids this token can reach, or "
            "copy it from the Railway dashboard URL "
            "(railway.com/project/<project_id>), then pass project_id."
        )

    async def resolve_target(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> RailwayTarget:
        """All three ids, saying exactly which one is missing."""
        project_id = await self.resolve_project_id(arguments, context)
        binding = await self._binding(context)

        resolved: dict[str, str] = {"project_id": project_id}
        for key in ("environment_id", "service_id"):
            candidate = (
                arguments.get(key) or binding.get(key) or self._defaults.get(key)
            )
            if not candidate:
                raise ValidationError(
                    f"No {key} given, and none is configured for this project. "
                    f"Call railway_project_info to list the ids of project "
                    f"{project_id}, then pass {key}."
                )
            resolved[key] = str(candidate)

        return RailwayTarget(**resolved)

    @abstractmethod
    async def run(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        """Do the work. Subclasses implement this instead of ``execute``."""

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        """Translate Railway and validation failures into a readable result."""
        try:
            return await self.run(arguments, context)
        except ValidationError as exc:
            return ToolResult.failure(exc.message)
        except RailwayApiError as exc:
            return ToolResult.failure(
                exc.message, status=exc.status, retryable=exc.retryable
            )


class RailwayListProjectsTool(RailwayTool):
    name = "railway_list_projects"
    description = (
        "List the Railway projects this token can see, with their ids. Use it "
        "when the project id is unknown — every other Railway tool needs one."
    )
    permission = PermissionLevel.READ
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "projects": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["count", "projects"],
    }

    async def run(self, arguments, context) -> ToolResult:
        projects = await self.client.list_projects(
            limit=int(arguments.get("limit", 20))
        )
        return ToolResult.success(
            {"count": len(projects), "projects": projects}, count=len(projects)
        )


class RailwayProjectInfoTool(RailwayTool):
    name = "railway_project_info"
    description = (
        "Read a Railway project: its name, its services and its environments, "
        "each with the id needed to address it. Use this before deploying, to "
        "get the service and environment ids — never guess them."
    )
    permission = PermissionLevel.READ
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"project_id": _ID_PROPERTIES["project_id"]},
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "project_id": {"type": "string"},
            "name": {"type": "string"},
            "services": {"type": "array", "items": {"type": "object"}},
            "environments": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["project_id"],
    }

    async def run(self, arguments, context) -> ToolResult:
        project_id = await self.resolve_project_id(arguments, context)
        info = await self.client.get_project(project_id)
        return ToolResult.success(
            info,
            project_id=info.get("project_id"),
            service_count=len(info.get("services") or []),
        )


class RailwayDeploymentsTool(RailwayTool):
    name = "railway_deployments"
    description = (
        "List recent deployments of a Railway service, newest first, with the "
        "real status of each. Use this to answer whether the last deploy "
        "succeeded — do not infer that from having triggered one."
    )
    permission = PermissionLevel.READ
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            **_ID_PROPERTIES,
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "deployments": {"type": "array", "items": {"type": "object"}},
            "latest_status": {"type": "string"},
        },
        "required": ["count", "deployments"],
    }

    async def run(self, arguments, context) -> ToolResult:
        target = await self.resolve_target(arguments, context)
        deployments = await self.client.list_deployments(
            project_id=target.project_id,
            environment_id=target.environment_id,
            service_id=target.service_id,
            limit=int(arguments.get("limit", 5)),
        )
        latest = deployments[0] if deployments else None
        return ToolResult.success(
            {
                "service_id": target.service_id,
                "environment_id": target.environment_id,
                "count": len(deployments),
                "latest_status": latest.get("status") if latest else None,
                "deployments": deployments,
            },
            count=len(deployments),
            latest_status=latest.get("status") if latest else None,
        )


class RailwayLogsTool(RailwayTool):
    name = "railway_deployment_logs"
    description = (
        "Read the tail of a Railway deployment's log. Use it to find out why a "
        "deployment failed. Secrets are masked before the text is returned."
    )
    permission = PermissionLevel.READ
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "deployment_id": {
                "type": "string",
                "description": "Deployment id, from railway_deployments.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        },
        "required": ["deployment_id"],
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "deployment_id": {"type": "string"},
            "count": {"type": "integer"},
            "lines": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["deployment_id", "count", "lines"],
    }

    async def run(self, arguments, context) -> ToolResult:
        deployment_id = str(arguments["deployment_id"])
        lines = await self.client.deployment_logs(
            deployment_id, limit=int(arguments.get("limit", 50))
        )
        return ToolResult.success(
            {
                "deployment_id": deployment_id,
                "count": len(lines),
                "lines": lines,
            },
            count=len(lines),
        )


class RailwayDeployTool(RailwayTool):
    """Deploy a service — the one tool here that changes the world.

    ``CRITICAL`` rather than ``EXECUTE``: the permission policy allows a
    configuration to make ``EXECUTE`` automatic, and no configuration should be
    able to make a production deploy automatic. It therefore always stops for a
    human.

    Triggering is not deploying. The mutation returns as soon as Railway has
    accepted the request, long before anything is live, so this waits for a
    terminal status and reports what it actually saw — including "still
    building", which is neither a success nor a failure and is reported as
    itself.
    """

    name = "railway_deploy"
    description = (
        "Deploy a Railway service. This affects real users and always requires "
        "human approval. It waits for the deployment to finish and reports the "
        "real status from Railway — a triggered deploy is not a finished one."
    )
    permission = PermissionLevel.CRITICAL
    idempotent = False
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            **_ID_PROPERTIES,
            "wait_seconds": {
                "type": "number",
                "minimum": 0,
                "maximum": 300,
                "description": (
                    "How long to wait for the deployment to finish before "
                    "reporting. 0 returns as soon as it is triggered; check "
                    "the outcome later with railway_deployments."
                ),
            },
        },
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "deployment_id": {"type": "string"},
            "status": {"type": "string"},
            "outcome": {"type": "string"},
            "live": {"type": "boolean"},
            "url": {"type": "string"},
            "waited_seconds": {"type": "number"},
            "note": {"type": "string"},
        },
        "required": ["outcome", "live"],
    }

    def __init__(
        self,
        client: RailwayClient | None = None,
        *,
        defaults: dict[str, str | None] | None = None,
        wait_seconds: float = DEFAULT_WAIT_SECONDS,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        super().__init__(client, defaults=defaults)
        self._wait_seconds = max(0.0, wait_seconds)
        self._poll_seconds = max(0.5, poll_seconds)
        # The registry kills a tool at its timeout. Give the wait room to
        # finish and report, rather than being cut off holding the answer.
        self.timeout_seconds = self._wait_seconds + TIMEOUT_MARGIN_SECONDS

    async def summarize(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> str | None:
        """Say what this deploy touches, in the words a person needs to judge it.

        Every id here can come from configuration, so the approval card would
        otherwise show an empty argument list against the word CRITICAL. The
        ids are resolved to their real names where Railway will say what they
        are, and fall back to the ids where it will not — an unnamed target is
        still far better than none.
        """
        try:
            target = await self.resolve_target(arguments, context)
        except ValidationError:
            # Nothing to describe yet; the run will fail on the same problem
            # and say so properly.
            return None

        names = await self._name_target(target)
        return (
            f"Deploy the service {names['service']} to the "
            f"{names['environment']} environment of project {names['project']} "
            "on Railway. This replaces what is currently running there and is "
            "visible to real users as soon as the build finishes."
        )

    async def _name_target(self, target: RailwayTarget) -> dict[str, str]:
        """Human names for the three ids, best effort."""
        fallback = {
            "project": target.project_id,
            "environment": target.environment_id,
            "service": target.service_id,
        }
        try:
            project = await self.client.get_project(target.project_id)
        except RailwayApiError:
            return fallback

        def named(entries: Any, wanted: str, default: str) -> str:
            for entry in entries or []:
                if entry.get("id") == wanted and entry.get("name"):
                    return f"'{entry['name']}'"
            return default

        return {
            "project": (
                f"'{project['name']}'" if project.get("name") else target.project_id
            ),
            "environment": named(
                project.get("environments"),
                target.environment_id,
                target.environment_id,
            ),
            "service": named(
                project.get("services"), target.service_id, target.service_id
            ),
        }

    async def run(self, arguments, context) -> ToolResult:
        target = await self.resolve_target(arguments, context)
        requested = arguments.get("wait_seconds")
        budget = (
            self._wait_seconds
            if requested is None
            else min(float(requested), self._wait_seconds)
        )

        triggered = await self.client.deploy(
            service_id=target.service_id, environment_id=target.environment_id
        )
        observed = await self._await_outcome(target, triggered, budget)

        return ToolResult.success(
            {
                "project_id": target.project_id,
                "environment_id": target.environment_id,
                "service_id": target.service_id,
                "triggered_at": triggered["triggered_at"],
                **observed,
            },
            deployment_id=observed.get("deployment_id"),
            status=observed.get("status"),
            outcome=observed.get("outcome"),
            live=observed.get("live"),
            target={
                "project_id": target.project_id,
                "environment_id": target.environment_id,
                "service_id": target.service_id,
            },
        )

    async def _await_outcome(
        self, target: RailwayTarget, triggered: dict[str, Any], budget: float
    ) -> dict[str, Any]:
        """Poll until the deployment settles, the budget runs out, or never.

        A Railway error while polling does not fail the deploy — the deploy was
        already accepted. It is reported as an unconfirmed outcome, which is
        the truth.
        """
        deployment_id = triggered.get("deployment_id")
        waited = 0.0
        latest: dict[str, Any] | None = None

        while True:
            try:
                latest = await self._find_deployment(target, deployment_id)
            except RailwayApiError as exc:
                return _observation(
                    latest,
                    deployment_id,
                    waited,
                    note=(
                        "The deploy was accepted, but its status could not be "
                        f"read back: {exc.message} Check railway_deployments."
                    ),
                )

            outcome = classify_status((latest or {}).get("status"))
            if outcome in ("success", "failure"):
                return _observation(latest, deployment_id, waited)
            if waited >= budget:
                return _observation(
                    latest,
                    deployment_id,
                    waited,
                    note=(
                        f"Still {(latest or {}).get('status') or 'unreported'} "
                        f"after {waited:.0f}s. The deploy is running but is not "
                        "confirmed live; check railway_deployments in a few "
                        "minutes."
                    ),
                )
            await asyncio.sleep(min(self._poll_seconds, budget - waited))
            waited += self._poll_seconds

    async def _find_deployment(
        self, target: RailwayTarget, deployment_id: str | None
    ) -> dict[str, Any] | None:
        """The deployment we triggered, by id when Railway gave one."""
        deployments = await self.client.list_deployments(
            project_id=target.project_id,
            environment_id=target.environment_id,
            service_id=target.service_id,
            limit=5,
        )
        if deployment_id:
            for deployment in deployments:
                if deployment.get("deployment_id") == deployment_id:
                    return deployment
            return None
        # No id came back from the mutation: the newest deployment of this
        # service is the one just triggered.
        return deployments[0] if deployments else None

    async def verify(
        self,
        arguments: dict[str, Any],
        result: ToolResult,
        context: ToolContext,
    ) -> ToolVerification:
        """Ask Railway again, after the fact, whether this is really live."""
        if not result.ok:
            return ToolVerification.failure(result.error or "deploy failed")

        raw_target = result.evidence.get("target") or {}
        try:
            target = RailwayTarget(**raw_target)
        except TypeError:
            return ToolVerification.failure(
                "The deploy did not record which service it targeted, so it "
                "cannot be confirmed."
            )

        try:
            latest = await self._find_deployment(
                target, result.evidence.get("deployment_id")
            )
        except RailwayApiError as exc:
            return ToolVerification.failure(
                f"Could not confirm the deployment: {exc.message}"
            )

        if latest is None:
            return ToolVerification.failure(
                "Railway reports no deployment for this service, so the "
                "trigger did not produce one."
            )

        status = latest.get("status")
        outcome = classify_status(status)
        if outcome == "success":
            url = latest.get("url")
            return ToolVerification.success(
                f"deployment {latest.get('deployment_id')} is {status}"
                + (f" at {url}" if url else "")
            )
        if outcome == "failure":
            return ToolVerification.failure(
                f"Railway reports the deployment as {status}. It is not live."
            )
        # Still building, or a status this client does not recognise. Claiming
        # success here is the exact failure mode verification exists to stop;
        # the tool output already told the model what is actually happening.
        return ToolVerification.skipped(
            f"deployment {latest.get('deployment_id')} is {status} — "
            "not yet confirmed live"
        )


def _observation(
    latest: dict[str, Any] | None,
    deployment_id: str | None,
    waited: float,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    """One shape for everything the deploy tool reports about the outcome."""
    status = (latest or {}).get("status")
    outcome = classify_status(status)
    observation: dict[str, Any] = {
        "deployment_id": (latest or {}).get("deployment_id") or deployment_id,
        "status": status,
        "outcome": outcome,
        # The single field that answers "did it work?", and it is only ever
        # true when Railway itself says the deployment succeeded.
        "live": outcome == "success",
        "url": (latest or {}).get("url"),
        "waited_seconds": round(waited, 1),
    }
    if note:
        observation["note"] = note
    elif outcome == "failure":
        observation["note"] = (
            "The deployment failed. Read railway_deployment_logs for this "
            "deployment id to find out why."
        )
    return observation


def railway_tools(
    *,
    token: SecretStr | str | None = None,
    token_kind: TokenKind = "account",
    api_url: str | None = None,
    timeout_seconds: float = 30.0,
    project_id: str | None = None,
    environment_id: str | None = None,
    service_id: str | None = None,
    deploy_wait_seconds: float = DEFAULT_WAIT_SECONDS,
    client: RailwayClient | None = None,
) -> list[Tool]:
    """Every Railway tool, sharing one client configuration."""
    shared = client or RailwayClient(
        token=token,
        token_kind=token_kind,
        api_url=api_url or DEFAULT_API_URL,
        timeout_seconds=timeout_seconds,
    )
    defaults = {
        "project_id": project_id,
        "environment_id": environment_id,
        "service_id": service_id,
    }
    return [
        RailwayListProjectsTool(shared, defaults=defaults),
        RailwayProjectInfoTool(shared, defaults=defaults),
        RailwayDeploymentsTool(shared, defaults=defaults),
        RailwayLogsTool(shared, defaults=defaults),
        RailwayDeployTool(
            shared, defaults=defaults, wait_seconds=deploy_wait_seconds
        ),
    ]
