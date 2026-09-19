"""Railway integration: GraphQL client, deploy approval, and honest verification.

The HTTP layer is a mock transport, so these run offline and deterministically.

The tests that matter most here are not the happy paths. They are the ones that
pin down what the system says when it does *not* know: a GraphQL error arriving
with HTTP 200, a deployment still building when the wait runs out, a log line
carrying a secret. Getting any of those wrong produces a confident lie about
production.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.enums import PermissionLevel, PermissionMode, VerificationStatus
from ulugbek_ai.integrations.railway.client import (
    PROJECT_TOKEN_HEADER,
    RailwayApiError,
    RailwayClient,
    classify_status,
)
from ulugbek_ai.integrations.railway.tools import (
    RailwayDeploymentsTool,
    RailwayDeployTool,
    RailwayListProjectsTool,
    RailwayLogsTool,
    RailwayProjectInfoTool,
    railway_tools,
)
from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.schemas import ProjectCreate
from ulugbek_ai.tools.base import ToolContext
from ulugbek_ai.tools.registry import build_default_registry

TOKEN = "railway_thisisasecrettokenvalue0123456789"
PROJECT = "11111111-1111-1111-1111-111111111111"
ENVIRONMENT = "22222222-2222-2222-2222-222222222222"
SERVICE = "33333333-3333-3333-3333-333333333333"

TARGET = {
    "project_id": PROJECT,
    "environment_id": ENVIRONMENT,
    "service_id": SERVICE,
}


def client_with(handler, **kwargs: Any) -> RailwayClient:
    kwargs.setdefault("token", TOKEN)
    return RailwayClient(transport=httpx.MockTransport(handler), **kwargs)


def data(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"data": payload})


def graphql_errors(*messages: str) -> httpx.Response:
    """Railway reporting a failure the way GraphQL does: HTTP 200."""
    return httpx.Response(200, json={"errors": [{"message": m} for m in messages]})


def deployment_node(
    deployment_id: str = "dep-1",
    status: str = "SUCCESS",
    url: str | None = "https://app.up.railway.app",
) -> dict[str, Any]:
    return {
        "id": deployment_id,
        "status": status,
        "createdAt": "2026-09-19T16:00:00Z",
        "staticUrl": url,
        "url": None,
    }


def deployments_payload(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"deployments": {"edges": [{"node": node} for node in nodes]}}


def operation_of(request: httpx.Request) -> str:
    """Which GraphQL operation a request carries."""
    import json

    return json.loads(request.content)["query"]


# --------------------------------------------------------------------------- #
# Status classification
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("SUCCESS", "success"),
        ("success", "success"),
        ("FAILED", "failure"),
        ("CRASHED", "failure"),
        ("BUILDING", "in_progress"),
        ("DEPLOYING", "in_progress"),
        ("QUEUED", "in_progress"),
        ("REMOVED", "inactive"),
        ("SLEEPING", "inactive"),
        (None, "unknown"),
        ("", "unknown"),
    ],
)
def test_statuses_are_classified(status: str | None, expected: str) -> None:
    assert classify_status(status) == expected


def test_an_unknown_status_is_never_read_as_success() -> None:
    """Railway adding a status must not silently become a green deploy."""
    assert classify_status("SOME_NEW_STATUS_RAILWAY_ADDS") == "unknown"


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
async def test_an_account_token_is_sent_as_a_bearer() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["project_header"] = request.headers.get(PROJECT_TOKEN_HEADER)
        return data({"me": {"id": "u1", "name": "Ulugbek"}})

    await client_with(handler).whoami()

    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["project_header"] is None


async def test_a_project_token_uses_its_own_header_and_not_a_bearer() -> None:
    """The two auth surfaces are not interchangeable; sending both is wrong."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["project_header"] = request.headers.get(PROJECT_TOKEN_HEADER)
        return data({"me": {"id": "u1", "name": "Ulugbek"}})

    await client_with(handler, token_kind="project").whoami()

    assert seen["project_header"] == TOKEN
    assert seen["auth"] is None


async def test_without_a_token_the_message_says_what_to_set() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be attempted without a token")

    with pytest.raises(RailwayApiError) as exc_info:
        await RailwayClient(
            token=None, transport=httpx.MockTransport(handler)
        ).whoami()

    assert "RAILWAY_TOKEN" in exc_info.value.message


async def test_a_rejected_credential_names_the_token_kind_to_try() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": [{"message": "Not Authorized"}]})

    with pytest.raises(RailwayApiError) as exc_info:
        await client_with(handler).whoami()

    assert "RAILWAY_TOKEN_KIND=project" in exc_info.value.message
    assert exc_info.value.status == 401


# --------------------------------------------------------------------------- #
# GraphQL error handling — the failure mode a naive client misses
# --------------------------------------------------------------------------- #
async def test_a_graphql_error_arriving_with_http_200_is_still_a_failure() -> None:
    """GraphQL reports failure in the body. HTTP 200 does not mean it worked."""

    def handler(request: httpx.Request) -> httpx.Response:
        return graphql_errors("Problem processing request")

    with pytest.raises(RailwayApiError) as exc_info:
        await client_with(handler).get_project(PROJECT)

    assert "Problem processing request" in exc_info.value.message


async def test_a_not_authorized_graphql_error_explains_the_likely_cause() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return graphql_errors("Not Authorized")

    with pytest.raises(RailwayApiError) as exc_info:
        await client_with(handler).get_project(PROJECT)

    assert exc_info.value.status == 403
    assert "cannot see this resource" in exc_info.value.message


async def test_a_not_found_graphql_error_points_at_the_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return graphql_errors("Project not found")

    with pytest.raises(RailwayApiError) as exc_info:
        await client_with(handler).get_project(PROJECT)

    assert exc_info.value.status == 404
    assert "Check the id" in exc_info.value.message


async def test_a_server_error_is_marked_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    with pytest.raises(RailwayApiError) as exc_info:
        await client_with(handler).whoami()

    assert exc_info.value.retryable is True


async def test_an_error_message_never_contains_the_token() -> None:
    """The one failure mode that would be unforgivable."""

    def handler(request: httpx.Request) -> httpx.Response:
        return graphql_errors(f"Bad token: {TOKEN}")

    with pytest.raises(RailwayApiError) as exc_info:
        await client_with(handler).whoami()

    assert TOKEN not in exc_info.value.message
    assert TOKEN not in str(exc_info.value.details)


# --------------------------------------------------------------------------- #
# Projections
# --------------------------------------------------------------------------- #
async def test_a_project_is_projected_to_its_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return data(
            {
                "project": {
                    "id": PROJECT,
                    "name": "ulugbek-ai",
                    "services": {"edges": [{"node": {"id": SERVICE, "name": "api"}}]},
                    "environments": {
                        "edges": [{"node": {"id": ENVIRONMENT, "name": "production"}}]
                    },
                }
            }
        )

    info = await client_with(handler).get_project(PROJECT)

    assert info["name"] == "ulugbek-ai"
    assert info["services"] == [{"id": SERVICE, "name": "api"}]
    assert info["environments"] == [{"id": ENVIRONMENT, "name": "production"}]


async def test_deployments_carry_a_classified_outcome() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return data(
            deployments_payload(
                deployment_node("dep-2", "BUILDING"),
                deployment_node("dep-1", "SUCCESS"),
            )
        )

    deployments = await client_with(handler).list_deployments(**TARGET)

    assert [d["outcome"] for d in deployments] == ["in_progress", "success"]
    assert deployments[1]["url"] == "https://app.up.railway.app"


# --------------------------------------------------------------------------- #
# Logs — the largest leak surface in the application
# --------------------------------------------------------------------------- #
async def test_a_secret_echoed_in_a_build_log_is_masked() -> None:
    """Build output echoes environment variables as a matter of course."""
    leaked = "ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnop"

    def handler(request: httpx.Request) -> httpx.Response:
        return data(
            {
                "deploymentLogs": [
                    {"timestamp": "t", "severity": "info", "message": leaked}
                ]
            }
        )

    lines = await client_with(handler).deployment_logs("dep-1")

    assert "sk-ant-api03-abcdefghijklmnop" not in lines[0]["message"]
    assert "ANTHROPIC_API_KEY" in lines[0]["message"]


async def test_a_long_log_line_is_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return data(
            {
                "deploymentLogs": [
                    {"timestamp": "t", "severity": "info", "message": "x" * 5_000}
                ]
            }
        )

    lines = await client_with(handler).deployment_logs("dep-1")

    assert len(lines[0]["message"]) < 500


# --------------------------------------------------------------------------- #
# Permission: a deploy can never be made automatic
# --------------------------------------------------------------------------- #
def test_deploy_is_critical() -> None:
    assert RailwayDeployTool().permission is PermissionLevel.CRITICAL
    assert RailwayDeployTool().idempotent is False


def test_no_configuration_can_make_a_deploy_automatic() -> None:
    """Even a policy that sets every level to auto leaves CRITICAL asking."""
    settings = Settings(
        _env_file=None,
        permission_read=PermissionMode.AUTO,
        permission_write=PermissionMode.AUTO,
        permission_execute=PermissionMode.AUTO,
        permission_delete=PermissionMode.AUTO,
        permission_critical=PermissionMode.AUTO,
    )

    policy = settings.permission_policy()

    assert policy[PermissionLevel.CRITICAL] is PermissionMode.APPROVAL


def test_the_registry_exposes_the_deploy_tool_as_critical() -> None:
    settings = Settings(_env_file=None, railway_token=TOKEN)
    registry = build_default_registry(
        permissions=settings.permission_policy(), settings=settings
    )

    described = {t["name"]: t for t in registry.describe_all()}

    assert described["railway_deploy"]["permission"] == "CRITICAL"
    assert described["railway_deploy"]["service"] == "railway"
    assert described["railway_deployments"]["permission"] == "READ"


# --------------------------------------------------------------------------- #
# Resolving the three ids
# --------------------------------------------------------------------------- #
async def test_ids_come_from_the_project_binding(session: AsyncSession) -> None:
    """The point of the Project system: the operator never pastes an id."""
    project = await ProjectManager(session).create(
        ProjectCreate(
            name="ULUGBEK AI",
            integrations={"railway": TARGET},
        )
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["variables"] = json.loads(request.content)["variables"]
        return data(deployments_payload(deployment_node()))

    tool = RailwayDeploymentsTool(client_with(handler))
    result = await tool.execute(
        {}, ToolContext(session=session, project_id=project.id)
    )

    assert result.ok
    assert seen["variables"]["input"]["serviceId"] == SERVICE


async def test_an_explicit_argument_beats_the_binding(session: AsyncSession) -> None:
    project = await ProjectManager(session).create(
        ProjectCreate(name="ULUGBEK AI", integrations={"railway": TARGET})
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["variables"] = json.loads(request.content)["variables"]
        return data(deployments_payload(deployment_node()))

    tool = RailwayDeploymentsTool(client_with(handler))
    result = await tool.execute(
        {"service_id": "other-service"},
        ToolContext(session=session, project_id=project.id),
    )

    assert result.ok
    assert seen["variables"]["input"]["serviceId"] == "other-service"


async def test_settings_defaults_are_used_when_nothing_else_is_configured() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["variables"] = json.loads(request.content)["variables"]
        return data(deployments_payload(deployment_node()))

    tool = RailwayDeploymentsTool(client_with(handler), defaults=TARGET)
    result = await tool.execute({}, ToolContext())

    assert result.ok
    assert seen["variables"]["input"]["projectId"] == PROJECT


async def test_a_missing_id_names_itself_and_how_to_find_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be made without ids")

    tool = RailwayDeploymentsTool(
        client_with(handler), defaults={"project_id": PROJECT}
    )
    result = await tool.execute({}, ToolContext())

    assert result.ok is False
    assert "environment_id" in result.error
    assert "railway_project_info" in result.error


async def test_a_missing_project_id_points_at_the_listing_tool() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be made without a project")

    result = await RailwayProjectInfoTool(client_with(handler)).execute(
        {}, ToolContext()
    )

    assert result.ok is False
    assert "railway_list_projects" in result.error


# --------------------------------------------------------------------------- #
# Deploying: triggering is not deploying
# --------------------------------------------------------------------------- #
async def test_a_successful_deploy_reports_the_status_railway_gives() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = operation_of(request)
        if "serviceInstanceDeployV2" in query:
            calls.append("deploy")
            return data({"serviceInstanceDeployV2": "dep-9"})
        calls.append("deployments")
        return data(deployments_payload(deployment_node("dep-9", "SUCCESS")))

    tool = RailwayDeployTool(client_with(handler), defaults=TARGET)
    result = await tool.execute({}, ToolContext())

    assert result.ok
    assert result.output["live"] is True
    assert result.output["status"] == "SUCCESS"
    assert result.output["url"] == "https://app.up.railway.app"
    assert calls == ["deploy", "deployments"]


async def test_a_failed_deploy_is_not_live_and_says_where_to_look() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "serviceInstanceDeployV2" in operation_of(request):
            return data({"serviceInstanceDeployV2": "dep-9"})
        return data(deployments_payload(deployment_node("dep-9", "FAILED")))

    tool = RailwayDeployTool(client_with(handler), defaults=TARGET)
    result = await tool.execute({}, ToolContext())

    # The tool call succeeded; the deployment did not. Those are different
    # facts and the output keeps them apart.
    assert result.ok
    assert result.output["live"] is False
    assert result.output["outcome"] == "failure"
    assert "railway_deployment_logs" in result.output["note"]


async def test_a_deploy_still_building_is_reported_as_still_building() -> None:
    """Never a success, never a failure — the truth, plus where to check."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "serviceInstanceDeployV2" in operation_of(request):
            return data({"serviceInstanceDeployV2": "dep-9"})
        return data(deployments_payload(deployment_node("dep-9", "BUILDING")))

    tool = RailwayDeployTool(
        client_with(handler), defaults=TARGET, wait_seconds=0, poll_seconds=0.5
    )
    result = await tool.execute({}, ToolContext())

    assert result.ok
    assert result.output["live"] is False
    assert result.output["outcome"] == "in_progress"
    assert "not confirmed live" in result.output["note"]


async def test_a_deploy_waits_for_a_terminal_status() -> None:
    """A deployment that is building when first polled is followed to the end."""
    statuses = iter(["BUILDING", "DEPLOYING", "SUCCESS"])

    def handler(request: httpx.Request) -> httpx.Response:
        if "serviceInstanceDeployV2" in operation_of(request):
            return data({"serviceInstanceDeployV2": "dep-9"})
        return data(deployments_payload(deployment_node("dep-9", next(statuses))))

    tool = RailwayDeployTool(
        client_with(handler), defaults=TARGET, wait_seconds=10, poll_seconds=0.01
    )
    result = await tool.execute({}, ToolContext())

    assert result.output["live"] is True
    assert result.output["status"] == "SUCCESS"


async def test_the_deployment_is_matched_by_id_not_by_being_newest() -> None:
    """Another deploy landing first must not be mistaken for ours."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "serviceInstanceDeployV2" in operation_of(request):
            return data({"serviceInstanceDeployV2": "dep-mine"})
        return data(
            deployments_payload(
                deployment_node("dep-someone-else", "SUCCESS"),
                deployment_node("dep-mine", "FAILED"),
            )
        )

    tool = RailwayDeployTool(client_with(handler), defaults=TARGET)
    result = await tool.execute({}, ToolContext())

    assert result.output["deployment_id"] == "dep-mine"
    assert result.output["live"] is False


async def test_a_deploy_whose_status_cannot_be_read_says_so() -> None:
    """The deploy was accepted; only the confirmation failed. Say that."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "serviceInstanceDeployV2" in operation_of(request):
            return data({"serviceInstanceDeployV2": "dep-9"})
        return graphql_errors("Problem processing request")

    tool = RailwayDeployTool(client_with(handler), defaults=TARGET)
    result = await tool.execute({}, ToolContext())

    assert result.ok
    assert result.output["live"] is False
    assert "could not be read back" in result.output["note"]


async def test_a_refused_deploy_is_a_failed_tool_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return graphql_errors("Not Authorized")

    tool = RailwayDeployTool(client_with(handler), defaults=TARGET)
    result = await tool.execute({}, ToolContext())

    assert result.ok is False
    assert result.evidence["status"] == 403


# --------------------------------------------------------------------------- #
# Verification: asking Railway, not trusting the trigger
# --------------------------------------------------------------------------- #
async def test_verification_confirms_a_live_deployment() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "serviceInstanceDeployV2" in operation_of(request):
            return data({"serviceInstanceDeployV2": "dep-9"})
        return data(deployments_payload(deployment_node("dep-9", "SUCCESS")))

    tool = RailwayDeployTool(client_with(handler), defaults=TARGET)
    result = await tool.execute({}, ToolContext())
    verification = await tool.verify({}, result, ToolContext())

    assert verification.status is VerificationStatus.SUCCESS
    assert "dep-9" in verification.reason


async def test_verification_fails_a_deployment_railway_calls_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "serviceInstanceDeployV2" in operation_of(request):
            return data({"serviceInstanceDeployV2": "dep-9"})
        return data(deployments_payload(deployment_node("dep-9", "CRASHED")))

    tool = RailwayDeployTool(client_with(handler), defaults=TARGET)
    result = await tool.execute({}, ToolContext())
    verification = await tool.verify({}, result, ToolContext())

    assert verification.status is VerificationStatus.FAILURE
    assert "not live" in verification.reason


async def test_verification_does_not_claim_success_while_still_building() -> None:
    """The exact failure mode verification exists to prevent."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "serviceInstanceDeployV2" in operation_of(request):
            return data({"serviceInstanceDeployV2": "dep-9"})
        return data(deployments_payload(deployment_node("dep-9", "BUILDING")))

    tool = RailwayDeployTool(
        client_with(handler), defaults=TARGET, wait_seconds=0, poll_seconds=0.01
    )
    result = await tool.execute({}, ToolContext())
    verification = await tool.verify({}, result, ToolContext())

    assert verification.status is not VerificationStatus.SUCCESS
    assert "not yet confirmed live" in verification.reason


async def test_verification_fails_when_the_trigger_produced_no_deployment() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "serviceInstanceDeployV2" in operation_of(request):
            return data({"serviceInstanceDeployV2": "dep-9"})
        return data(deployments_payload())

    tool = RailwayDeployTool(
        client_with(handler), defaults=TARGET, wait_seconds=0, poll_seconds=0.01
    )
    result = await tool.execute({}, ToolContext())
    verification = await tool.verify({}, result, ToolContext())

    assert verification.status is VerificationStatus.FAILURE
    assert "did not produce one" in verification.reason


# --------------------------------------------------------------------------- #
# Read tools
# --------------------------------------------------------------------------- #
async def test_listing_projects_returns_ids() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return data(
            {
                "projects": {
                    "edges": [
                        {"node": {"id": PROJECT, "name": "ulugbek-ai", "createdAt": "t"}}
                    ]
                }
            }
        )

    result = await RailwayListProjectsTool(client_with(handler)).execute(
        {}, ToolContext()
    )

    assert result.ok
    assert result.output["projects"][0]["project_id"] == PROJECT


async def test_reading_logs_returns_redacted_lines() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return data(
            {
                "deploymentLogs": [
                    {
                        "timestamp": "t",
                        "severity": "error",
                        "message": "GITHUB_TOKEN=ghp_secretvalue0123456789 rejected",
                    }
                ]
            }
        )

    result = await RailwayLogsTool(client_with(handler)).execute(
        {"deployment_id": "dep-1"}, ToolContext()
    )

    assert result.ok
    assert "ghp_secretvalue0123456789" not in str(result.output)


async def test_every_railway_tool_is_registered_once() -> None:
    names = [tool.name for tool in railway_tools(token=TOKEN)]

    assert names == [
        "railway_list_projects",
        "railway_project_info",
        "railway_deployments",
        "railway_deployment_logs",
        "railway_deploy",
    ]
    assert len(set(names)) == len(names)


async def test_the_deploy_tool_timeout_outlasts_its_own_wait() -> None:
    """Otherwise the registry kills the deploy while it holds the answer."""
    tool = RailwayDeployTool(wait_seconds=90)

    assert tool.timeout_seconds > 90


# --------------------------------------------------------------------------- #
# End to end: the approval gate is real, not cosmetic
# --------------------------------------------------------------------------- #
async def test_the_real_deploy_tool_reaches_railway_only_after_approval(
    session: AsyncSession, llm, settings: Settings
) -> None:
    """A deploy must not touch Railway until a human has said yes.

    The assertion that matters is the request count: a gate that pauses the
    run but has already deployed would pass every other test in this file.
    """
    from ulugbek_ai.agent.engine import AgentEngine
    from ulugbek_ai.agent.schemas import AgentRunRequest
    from ulugbek_ai.approvals.manager import ApprovalManager
    from ulugbek_ai.core.enums import RunStatus
    from ulugbek_ai.llm.scripted import text_response, tool_response
    from ulugbek_ai.tools.permissions import PermissionPolicy, PermissionService
    from tests.factories import plan_reply, verdict_reply

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = operation_of(request)
        requests.append("deploy" if "serviceInstanceDeployV2" in query else "read")
        if "serviceInstanceDeployV2" in query:
            return data({"serviceInstanceDeployV2": "dep-9"})
        return data(deployments_payload(deployment_node("dep-9", "SUCCESS")))

    registry = build_default_registry(
        permissions=PermissionService(PermissionPolicy.from_settings(settings))
    )
    for tool in railway_tools(client=client_with(handler), **TARGET):
        registry.register(tool)

    agent = AgentEngine(session, llm=llm, registry=registry, settings=settings)

    llm.queue(
        plan_reply(("Deploy the API", "railway_deploy", "it is live"), requires_tools=True)
    )
    llm.queue(tool_response("railway_deploy", {}))
    paused = await agent.run(AgentRunRequest(message="Deploy the API to production."))

    assert paused.status is RunStatus.WAITING_APPROVAL
    assert paused.approval.tool_name == "railway_deploy"
    assert paused.approval.permission == "CRITICAL"
    assert paused.output is None
    # Nothing has been deployed. Railway has been read — the approval card
    # names the service — but the mutation has not been sent.
    assert "deploy" not in requests

    await ApprovalManager(session).approve(
        paused.approval.approval_id, decided_by="ulugbek"
    )
    llm.queue(text_response("Deployed: dep-9 is live."))
    llm.queue(verdict_reply("SUCCESS", "railway reported the deployment SUCCESS"))
    resumed = await agent.resume(paused.run_id)

    assert resumed.status is RunStatus.COMPLETED
    assert "deploy" in requests
    assert resumed.tools_used == ["railway_deploy"]


async def test_a_rejected_deploy_never_reaches_railway(
    session: AsyncSession, llm, settings: Settings
) -> None:
    from ulugbek_ai.agent.engine import AgentEngine
    from ulugbek_ai.agent.schemas import AgentRunRequest
    from ulugbek_ai.approvals.manager import ApprovalManager
    from ulugbek_ai.core.enums import RunStatus
    from ulugbek_ai.llm.scripted import text_response, tool_response
    from ulugbek_ai.tools.permissions import PermissionPolicy, PermissionService
    from tests.factories import plan_reply, verdict_reply

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = operation_of(request)
        if "serviceInstanceDeployV2" in query:
            requests.append("deploy")
            return data({"serviceInstanceDeployV2": "dep-9"})
        requests.append("read")
        return data({"project": {"id": PROJECT, "name": "ulugbek-ai"}})

    registry = build_default_registry(
        permissions=PermissionService(PermissionPolicy.from_settings(settings))
    )
    for tool in railway_tools(client=client_with(handler), **TARGET):
        registry.register(tool)

    agent = AgentEngine(session, llm=llm, registry=registry, settings=settings)
    llm.queue(
        plan_reply(("Deploy", "railway_deploy", "it is live"), requires_tools=True)
    )
    llm.queue(tool_response("railway_deploy", {}))
    paused = await agent.run(AgentRunRequest(message="Deploy the API."))

    await ApprovalManager(session).reject(paused.approval.approval_id)
    llm.queue(text_response("I did not deploy: you rejected it."))
    llm.queue(verdict_reply("SUCCESS", "the rejection was reported honestly"))
    resumed = await agent.resume(paused.run_id)

    assert resumed.status is RunStatus.COMPLETED
    # The decisive assertion: a rejected deploy never became a deployment.
    assert "deploy" not in requests


# --------------------------------------------------------------------------- #
# The approval card has to say what the deploy does
# --------------------------------------------------------------------------- #
async def test_the_approval_reason_names_the_service_not_just_the_tool() -> None:
    """With every id configured, the arguments are empty — so words matter."""

    def handler(request: httpx.Request) -> httpx.Response:
        return data(
            {
                "project": {
                    "id": PROJECT,
                    "name": "ulugbek-ai",
                    "services": {"edges": [{"node": {"id": SERVICE, "name": "api"}}]},
                    "environments": {
                        "edges": [{"node": {"id": ENVIRONMENT, "name": "production"}}]
                    },
                }
            }
        )

    tool = RailwayDeployTool(client_with(handler), defaults=TARGET)
    summary = await tool.summarize({}, ToolContext())

    assert "'api'" in summary
    assert "'production'" in summary
    assert "'ulugbek-ai'" in summary
    assert "real users" in summary


async def test_the_approval_reason_falls_back_to_ids_when_railway_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return graphql_errors("Problem processing request")

    tool = RailwayDeployTool(client_with(handler), defaults=TARGET)
    summary = await tool.summarize({}, ToolContext())

    assert SERVICE in summary
    assert ENVIRONMENT in summary


async def test_no_summary_is_offered_when_the_target_is_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("nothing to ask Railway about")

    tool = RailwayDeployTool(client_with(handler))

    assert await tool.summarize({}, ToolContext()) is None


async def test_the_approval_the_operator_sees_describes_the_deploy(
    session: AsyncSession, llm, settings: Settings
) -> None:
    """End to end: what lands in the approval row is what the card renders."""
    from ulugbek_ai.agent.engine import AgentEngine
    from ulugbek_ai.agent.schemas import AgentRunRequest
    from ulugbek_ai.llm.scripted import tool_response
    from ulugbek_ai.tools.permissions import PermissionPolicy, PermissionService
    from tests.factories import plan_reply

    def handler(request: httpx.Request) -> httpx.Response:
        return data(
            {
                "project": {
                    "id": PROJECT,
                    "name": "ulugbek-ai",
                    "services": {"edges": [{"node": {"id": SERVICE, "name": "api"}}]},
                    "environments": {
                        "edges": [{"node": {"id": ENVIRONMENT, "name": "production"}}]
                    },
                }
            }
        )

    registry = build_default_registry(
        permissions=PermissionService(PermissionPolicy.from_settings(settings))
    )
    for tool in railway_tools(client=client_with(handler), **TARGET):
        registry.register(tool)

    agent = AgentEngine(session, llm=llm, registry=registry, settings=settings)
    llm.queue(
        plan_reply(("Deploy", "railway_deploy", "it is live"), requires_tools=True)
    )
    llm.queue(tool_response("railway_deploy", {}))
    paused = await agent.run(AgentRunRequest(message="Deploy the API."))

    from ulugbek_ai.approvals.repository import ApprovalRepository

    approval = await ApprovalRepository(session).get(paused.approval.approval_id)

    assert "'api'" in approval.reason
    assert "'production'" in approval.reason
    # The policy's own reason is kept, below the description.
    assert "approval" in approval.reason.lower()


async def test_a_summary_that_raises_does_not_block_the_approval(
    session: AsyncSession, llm, settings: Settings
) -> None:
    """A cosmetic failure must never stand between a human and a decision."""
    from ulugbek_ai.agent.engine import AgentEngine
    from ulugbek_ai.agent.schemas import AgentRunRequest
    from ulugbek_ai.core.enums import RunStatus
    from ulugbek_ai.llm.scripted import tool_response
    from ulugbek_ai.tools.permissions import PermissionPolicy, PermissionService
    from tests.factories import plan_reply

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("not reached")

    class BrokenSummary(RailwayDeployTool):
        async def summarize(self, arguments, context):
            raise RuntimeError("summary exploded")

    registry = build_default_registry(
        permissions=PermissionService(PermissionPolicy.from_settings(settings))
    )
    registry.register(BrokenSummary(client_with(handler), defaults=TARGET))

    agent = AgentEngine(session, llm=llm, registry=registry, settings=settings)
    llm.queue(
        plan_reply(("Deploy", "railway_deploy", "it is live"), requires_tools=True)
    )
    llm.queue(tool_response("railway_deploy", {}))
    paused = await agent.run(AgentRunRequest(message="Deploy the API."))

    assert paused.status is RunStatus.WAITING_APPROVAL
    assert paused.approval is not None
