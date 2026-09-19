"""A minimal Railway Public API client.

Railway's public API is GraphQL: one endpoint, POST only, and — the detail that
catches every naive client — **errors come back with HTTP 200**, inside an
``errors`` array. A client that only checks the status code reports a failed
deploy as a success, which is precisely the lie this project exists to prevent.

Three rules drive the rest:

1. **The token never leaves this module.** Held as a ``SecretStr``, sent only
   as a header, and absent from every message built here.
2. **Responses are projected, not forwarded.** A Railway payload carries far
   more than the agent needs; each method returns a small dict.
3. **Deployment logs are redacted before they exist as a Python string the rest
   of the system can see.** Build output routinely echoes environment
   variables, so this is the single largest leak surface in the application.
"""

from __future__ import annotations

import logging
from typing import Any, Final, Literal

import httpx
from pydantic import SecretStr

from ulugbek_ai.core.errors import ToolError
from ulugbek_ai.core.redaction import redact_text, truncate
from ulugbek_ai.core.utils import utcnow

logger = logging.getLogger(__name__)

DEFAULT_API_URL: Final[str] = "https://backboard.railway.com/graphql/v2"
USER_AGENT: Final[str] = "ulugbek-ai-agent"

#: Account, workspace and OAuth tokens authenticate with ``Authorization``;
#: a project token has its own header and must not be sent as a bearer.
TokenKind = Literal["account", "project"]
PROJECT_TOKEN_HEADER: Final[str] = "Project-Access-Token"

#: One log line, capped. A build log is unbounded and the model pays for it.
_LOG_LINE_LIMIT: Final[int] = 400
#: Free text from Railway (a commit message on a deployment, an error).
_TEXT_LIMIT: Final[int] = 300

DeploymentOutcome = Literal["success", "failure", "in_progress", "inactive", "unknown"]

#: Railway's deployment statuses, grouped by what they mean for the operator.
#: Anything unrecognised is reported verbatim as ``unknown`` rather than being
#: guessed into one of these buckets — a new status must never read as success.
_SUCCESS_STATUSES: Final[frozenset[str]] = frozenset({"SUCCESS"})
_FAILURE_STATUSES: Final[frozenset[str]] = frozenset({"FAILED", "CRASHED"})
_IN_PROGRESS_STATUSES: Final[frozenset[str]] = frozenset(
    {"BUILDING", "DEPLOYING", "INITIALIZING", "QUEUED", "WAITING", "NEEDS_APPROVAL"}
)
_INACTIVE_STATUSES: Final[frozenset[str]] = frozenset(
    {"REMOVED", "REMOVING", "SKIPPED", "SLEEPING"}
)


def classify_status(status: str | None) -> DeploymentOutcome:
    """What a deployment status means, without guessing about new ones."""
    if not status:
        return "unknown"
    upper = status.upper()
    if upper in _SUCCESS_STATUSES:
        return "success"
    if upper in _FAILURE_STATUSES:
        return "failure"
    if upper in _IN_PROGRESS_STATUSES:
        return "in_progress"
    if upper in _INACTIVE_STATUSES:
        return "inactive"
    return "unknown"


class RailwayApiError(ToolError):
    """A Railway request failed. ``message`` is always safe to show."""

    code = "railway_api_error"

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details={"status": status, **(details or {})})
        self.status = status
        self.retryable = retryable


# --------------------------------------------------------------- operations #
# Kept as named constants so every query the application can send is visible in
# one place, and a test can assert on the exact document that goes over the wire.

_ME_QUERY: Final[str] = """
query me { me { id name } }
"""

_PROJECTS_QUERY: Final[str] = """
query projects($first: Int) {
  projects(first: $first) {
    edges { node { id name createdAt } }
  }
}
"""

_PROJECT_QUERY: Final[str] = """
query project($id: String!) {
  project(id: $id) {
    id
    name
    services { edges { node { id name } } }
    environments { edges { node { id name } } }
  }
}
"""

_DEPLOYMENTS_QUERY: Final[str] = """
query deployments($input: DeploymentListInput!, $first: Int) {
  deployments(input: $input, first: $first) {
    edges { node { id status createdAt staticUrl url } }
  }
}
"""

_LOGS_QUERY: Final[str] = """
query deploymentLogs($deploymentId: String!, $limit: Int) {
  deploymentLogs(deploymentId: $deploymentId, limit: $limit) {
    timestamp
    message
    severity
  }
}
"""

_DEPLOY_MUTATION: Final[str] = """
mutation serviceInstanceDeployV2($serviceId: String!, $environmentId: String!) {
  serviceInstanceDeployV2(serviceId: $serviceId, environmentId: $environmentId)
}
"""


class RailwayClient:
    """Async Railway GraphQL client scoped to what the agent actually needs."""

    def __init__(
        self,
        *,
        token: SecretStr | str | None = None,
        token_kind: TokenKind = "account",
        api_url: str = DEFAULT_API_URL,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = (
            token
            if isinstance(token, SecretStr) or token is None
            else SecretStr(token)
        )
        self._token_kind: TokenKind = token_kind
        self._api_url = api_url
        self._timeout = timeout_seconds
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Any, **overrides: Any) -> "RailwayClient":
        return cls(
            token=settings.railway_token,
            token_kind=settings.railway_token_kind,
            api_url=settings.railway_api_url,
            timeout_seconds=settings.railway_timeout_seconds,
            **overrides,
        )

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }
        if self._token is None:
            return headers
        secret = self._token.get_secret_value()
        if self._token_kind == "project":
            headers[PROJECT_TOKEN_HEADER] = secret
        else:
            headers["Authorization"] = f"Bearer {secret}"
        return headers

    # -------------------------------------------------------------- GraphQL #
    async def _graphql(
        self, query: str, variables: dict[str, Any], *, operation: str
    ) -> dict[str, Any]:
        """Send one GraphQL document and return its ``data`` payload."""
        if self._token is None:
            raise RailwayApiError(
                "RAILWAY_TOKEN is not set, so Railway cannot be reached. Add it "
                "to the environment and restart the service. Every Railway "
                "operation needs a token; there is no anonymous access."
            )

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    self._api_url,
                    headers=self._headers(),
                    json={"query": query, "variables": variables},
                )
        except httpx.TimeoutException as exc:
            raise RailwayApiError(
                f"Railway did not respond within {self._timeout:.0f}s.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise RailwayApiError(
                f"Could not reach Railway: {redact_text(str(exc))}", retryable=True
            ) from exc

        if response.status_code >= 400:
            raise self._http_error(response, operation)

        try:
            payload = response.json()
        except ValueError as exc:
            raise RailwayApiError(
                "Railway returned a response that was not JSON."
            ) from exc

        # GraphQL reports failures with HTTP 200 and an errors array. Checking
        # only the status code here would turn a refused deploy into a success.
        errors = payload.get("errors")
        if errors:
            raise self._graphql_error(errors, operation)

        data = payload.get("data")
        if not isinstance(data, dict):
            raise RailwayApiError(
                f"Railway returned no data for {operation}."
            )
        return data

    def _http_error(self, response: httpx.Response, operation: str) -> RailwayApiError:
        status = response.status_code
        if status in (401, 403):
            hint = (
                "Check that RAILWAY_TOKEN is valid and has not expired."
                if self._token_kind == "account"
                else "Check RAILWAY_TOKEN, and that it belongs to this project."
            )
            wrong_header = (
                " If this is a project token, set RAILWAY_TOKEN_KIND=project; "
                "project tokens use their own header, not a bearer."
                if self._token_kind == "account"
                else " If this is an account token, set RAILWAY_TOKEN_KIND=account."
            )
            return RailwayApiError(
                f"Railway rejected the credential. {hint}{wrong_header}",
                status=status,
            )
        if status == 429:
            return RailwayApiError(
                "Railway rate limit reached. Wait before calling it again; "
                "retrying immediately will fail the same way.",
                status=status,
                retryable=True,
            )
        if status >= 500:
            return RailwayApiError(
                f"Railway is having trouble ({status}). This is on their side.",
                status=status,
                retryable=True,
            )
        return RailwayApiError(
            f"Railway refused {operation} with HTTP {status}.", status=status
        )

    def _graphql_error(
        self, errors: list[Any], operation: str
    ) -> RailwayApiError:
        messages = []
        for error in errors:
            if isinstance(error, dict):
                messages.append(str(error.get("message", "")))
            else:
                messages.append(str(error))
        detail = truncate(redact_text("; ".join(m for m in messages if m)), 300)
        lowered = detail.lower()

        if "not authorized" in lowered or "unauthorized" in lowered:
            return RailwayApiError(
                f"Railway refused {operation}: not authorized. The token is "
                "valid but cannot see this resource — check that the id "
                "belongs to the same account, and that the token's scope "
                f"covers it. ({detail})",
                status=403,
            )
        if "not found" in lowered:
            return RailwayApiError(
                f"Railway could not find what {operation} asked for: {detail}. "
                "Check the id, or list the project to see the real ids.",
                status=404,
            )
        return RailwayApiError(
            f"Railway rejected {operation}: {detail or 'no reason given'}."
        )

    # ------------------------------------------------------------ operations #
    async def whoami(self) -> dict[str, Any]:
        """Who the token belongs to — the cheapest proof it works."""
        data = await self._graphql(_ME_QUERY, {}, operation="me")
        me = data.get("me") or {}
        return {"id": me.get("id"), "name": me.get("name")}

    async def list_projects(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Every project the token can see.

        This is how the project id is discovered. Without it nothing else on
        Railway can be addressed, and a project-scoped token cannot run this —
        which the error from Railway says plainly.
        """
        data = await self._graphql(
            _PROJECTS_QUERY,
            {"first": max(1, min(int(limit), 50))},
            operation="projects",
        )
        edges = ((data.get("projects") or {}).get("edges")) or []
        out: list[dict[str, Any]] = []
        for edge in edges:
            node = (edge or {}).get("node") or {}
            if node.get("id"):
                out.append(
                    {
                        "project_id": node.get("id"),
                        "name": node.get("name"),
                        "created_at": node.get("createdAt"),
                    }
                )
        return out

    async def get_project(self, project_id: str) -> dict[str, Any]:
        """A project with its services and environments, ids included.

        The ids are the point: nothing else can be done on Railway without
        them, and this is where a human or the model looks them up.
        """
        data = await self._graphql(
            _PROJECT_QUERY, {"id": project_id}, operation="project"
        )
        project = data.get("project")
        if not isinstance(project, dict):
            raise RailwayApiError(
                f"Railway returned no project for id {project_id}.", status=404
            )
        return {
            "project_id": project.get("id"),
            "name": project.get("name"),
            "services": _nodes(project.get("services")),
            "environments": _nodes(project.get("environments")),
        }

    async def list_deployments(
        self,
        *,
        project_id: str,
        environment_id: str,
        service_id: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Recent deployments of one service, newest first."""
        data = await self._graphql(
            _DEPLOYMENTS_QUERY,
            {
                "input": {
                    "projectId": project_id,
                    "environmentId": environment_id,
                    "serviceId": service_id,
                },
                "first": max(1, min(int(limit), 20)),
            },
            operation="deployments",
        )
        edges = ((data.get("deployments") or {}).get("edges")) or []
        return [_deployment(edge.get("node") or {}) for edge in edges if edge]

    async def deployment_logs(
        self, deployment_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """The tail of a deployment's log, redacted line by line."""
        data = await self._graphql(
            _LOGS_QUERY,
            {"deploymentId": deployment_id, "limit": max(1, min(int(limit), 200))},
            operation="deploymentLogs",
        )
        lines = data.get("deploymentLogs") or []
        return [
            {
                "timestamp": line.get("timestamp"),
                "severity": line.get("severity"),
                # Build output echoes environment variables as a matter of
                # course. Redact before the text exists anywhere else.
                "message": truncate(
                    redact_text(str(line.get("message", ""))), _LOG_LINE_LIMIT
                ),
            }
            for line in lines
            if isinstance(line, dict)
        ]

    async def deploy(
        self, *, service_id: str, environment_id: str
    ) -> dict[str, Any]:
        """Trigger a deployment of one service instance.

        Returns the deployment id when Railway gives one. It may not: the
        mutation's return shape is a scalar, so the caller must be able to find
        the deployment by time as well as by id.
        """
        triggered_at = utcnow()
        data = await self._graphql(
            _DEPLOY_MUTATION,
            {"serviceId": service_id, "environmentId": environment_id},
            operation="serviceInstanceDeployV2",
        )
        returned = data.get("serviceInstanceDeployV2")
        deployment_id: str | None = None
        if isinstance(returned, str) and returned:
            deployment_id = returned
        elif isinstance(returned, dict):
            candidate = returned.get("id")
            deployment_id = str(candidate) if candidate else None

        return {
            "deployment_id": deployment_id,
            "service_id": service_id,
            "environment_id": environment_id,
            "triggered_at": triggered_at.isoformat(),
        }


def _nodes(connection: Any) -> list[dict[str, Any]]:
    """Flatten a GraphQL connection into ``[{id, name}]``."""
    edges = (connection or {}).get("edges") or []
    out: list[dict[str, Any]] = []
    for edge in edges:
        node = (edge or {}).get("node") or {}
        if node.get("id"):
            out.append({"id": node.get("id"), "name": node.get("name")})
    return out


def _deployment(node: dict[str, Any]) -> dict[str, Any]:
    """Project one deployment down to what an operator reads."""
    status = node.get("status")
    url = node.get("staticUrl") or node.get("url")
    return {
        "deployment_id": node.get("id"),
        "status": status,
        "outcome": classify_status(status),
        "created_at": node.get("createdAt"),
        "url": truncate(str(url), _TEXT_LIMIT) if url else None,
    }
