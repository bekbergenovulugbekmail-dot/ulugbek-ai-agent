"""HTTP surface: routing, validation, error shape and the approval round trip."""

from __future__ import annotations

from httpx import AsyncClient

from ulugbek_ai.core.enums import MemoryType, PermissionLevel
from ulugbek_ai.llm.scripted import ScriptedLLMClient, text_response, tool_response
from ulugbek_ai.tools.base import Tool, ToolResult
from tests.factories import plan_reply, verdict_reply


class PublishTool(Tool):
    name = "publish_post"
    description = "Publish a post to a social account."
    permission = PermissionLevel.CRITICAL

    async def execute(self, arguments, context) -> ToolResult:
        return ToolResult.success({"post_id": "p-1"}, post_id="p-1")


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
async def test_health(client: AsyncClient) -> None:
    response = await client.get("/api/health")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["database"]["connected"] is True
    assert body["tools"]["count"] >= 1


async def test_health_never_leaks_the_api_key(client: AsyncClient) -> None:
    body = (await client.get("/api/health")).json()
    assert set(body["llm"]) == {"configured", "model"}


async def test_tool_listing_exposes_permissions(client: AsyncClient) -> None:
    body = (await client.get("/api/health/tools")).json()
    permissions = {tool["name"]: tool["permission"] for tool in body["tools"]}

    assert permissions["memory_search"] == "READ"
    assert permissions["memory_write"] == "WRITE"


async def test_root_banner(client: AsyncClient) -> None:
    assert (await client.get("/")).status_code == 200


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
async def test_project_crud(client: AsyncClient) -> None:
    created = await client.post(
        "/api/projects",
        json={"name": "ERP", "description": "Warehouse", "keywords": ["invoice"]},
    )
    assert created.status_code == 201
    project = created.json()
    assert project["slug"] == "erp"

    listed = await client.get("/api/projects")
    assert [item["id"] for item in listed.json()] == [project["id"]]

    patched = await client.patch(
        f"/api/projects/{project['id']}", json={"environment": "staging"}
    )
    assert patched.json()["environment"] == "staging"


async def test_duplicate_project_is_a_409(client: AsyncClient) -> None:
    await client.post("/api/projects", json={"name": "ERP"})
    response = await client.post("/api/projects", json={"name": "ERP"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_unknown_project_is_a_404(client: AsyncClient) -> None:
    response = await client.get(
        "/api/projects/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_invalid_payload_is_a_422(client: AsyncClient) -> None:
    assert (await client.post("/api/projects", json={"name": ""})).status_code == 422


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
async def test_memory_crud_and_search(client: AsyncClient) -> None:
    created = await client.post(
        "/api/memory",
        json={
            "type": MemoryType.PREFERENCE.value,
            "content": "Reports should be written in Uzbek.",
            "importance": 0.9,
        },
    )
    assert created.status_code == 201
    memory_id = created.json()["id"]

    found = await client.get("/api/memory/search", params={"q": "reports language"})
    assert found.status_code == 200
    assert found.json()[0]["memory"]["id"] == memory_id
    assert found.json()[0]["score"] > 0

    assert (await client.delete(f"/api/memory/{memory_id}")).status_code == 204
    assert (await client.get("/api/memory")).json() == []


async def test_stored_memory_is_redacted(client: AsyncClient) -> None:
    response = await client.post(
        "/api/memory",
        json={
            "type": MemoryType.FACT.value,
            "content": "token is ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        },
    )
    assert "ghp_" not in response.json()["content"]


async def test_memory_search_requires_a_query(client: AsyncClient) -> None:
    assert (await client.get("/api/memory/search")).status_code == 422


# --------------------------------------------------------------------------- #
# Tasks
# --------------------------------------------------------------------------- #
async def test_task_creation_and_cancellation(client: AsyncClient) -> None:
    created = await client.post("/api/tasks", json={"goal": "Deploy the ERP"})
    assert created.status_code == 200
    task_id = created.json()["id"]
    assert created.json()["status"] == "PENDING"

    cancelled = await client.post(
        f"/api/tasks/{task_id}/cancel", params={"reason": "not now"}
    )
    assert cancelled.json()["status"] == "CANCELLED"


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #
async def test_agent_run(client: AsyncClient, llm: ScriptedLLMClient) -> None:
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("Tashkent."))
    llm.queue(verdict_reply("SUCCESS", "correct"))

    response = await client.post(
        "/api/agent/run", json={"message": "Capital of Uzbekistan?"}
    )
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "COMPLETED"
    assert body["output"] == "Tashkent."
    assert body["verification"]["status"] == "SUCCESS"


async def test_agent_run_trace_is_readable(
    client: AsyncClient, llm: ScriptedLLMClient
) -> None:
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("42"))
    llm.queue(verdict_reply())

    run_id = (
        await client.post("/api/agent/run", json={"message": "6x7?"})
    ).json()["run_id"]

    detail = (await client.get(f"/api/agent/runs/{run_id}")).json()

    assert detail["status"] == "COMPLETED"
    assert [step["type"] for step in detail["steps"]]
    assert detail["steps"][0]["sequence"] == 1


async def test_agent_run_requires_a_message(client: AsyncClient) -> None:
    assert (await client.post("/api/agent/run", json={})).status_code == 422


# --------------------------------------------------------------------------- #
# The approval round trip
# --------------------------------------------------------------------------- #
async def test_approval_round_trip_over_http(
    client: AsyncClient, llm: ScriptedLLMClient, registry
) -> None:
    """Pause on a CRITICAL action, approve it over HTTP, and finish the run."""
    registry.register(PublishTool())

    llm.queue(plan_reply(("Publish it", "publish_post", "a post id")))
    llm.queue(tool_response("publish_post", {}))
    paused = (
        await client.post("/api/agent/run", json={"message": "Publish the post."})
    ).json()

    assert paused["status"] == "WAITING_APPROVAL"
    assert paused["output"] is None
    approval_id = paused["approval"]["approval_id"]

    pending = (await client.get("/api/approvals", params={"status": "PENDING"})).json()
    assert [item["id"] for item in pending] == [approval_id]

    llm.queue(text_response("Published, id p-1."))
    llm.queue(verdict_reply("SUCCESS", "publish_post returned p-1"))
    resumed = (
        await client.post(
            f"/api/approvals/{approval_id}/approve", json={"decided_by": "ulugbek"}
        )
    ).json()

    assert resumed["status"] == "COMPLETED"
    assert resumed["tools_used"] == ["publish_post"]

    decided = (await client.get(f"/api/approvals/{approval_id}")).json()
    assert decided["status"] == "APPROVED"
    assert decided["decided_by"] == "ulugbek"


async def test_rejecting_over_http(
    client: AsyncClient, llm: ScriptedLLMClient, registry
) -> None:
    registry.register(PublishTool())

    llm.queue(plan_reply(("Publish it", "publish_post", "a post id")))
    llm.queue(tool_response("publish_post", {}))
    paused = (
        await client.post("/api/agent/run", json={"message": "Publish the post."})
    ).json()

    llm.queue(text_response("I did not publish it — the action was rejected."))
    llm.queue(verdict_reply("SUCCESS", "correctly reported the rejection"))
    resumed = (
        await client.post(
            f"/api/approvals/{paused['approval']['approval_id']}/reject",
            json={"note": "wrong account"},
        )
    ).json()

    assert resumed["status"] == "COMPLETED"
    assert "did not publish" in resumed["output"]


async def test_deciding_twice_is_a_409(
    client: AsyncClient, llm: ScriptedLLMClient, registry
) -> None:
    registry.register(PublishTool())

    llm.queue(plan_reply(("Publish it", "publish_post", "a post id")))
    llm.queue(tool_response("publish_post", {}))
    paused = (
        await client.post("/api/agent/run", json={"message": "Publish the post."})
    ).json()
    approval_id = paused["approval"]["approval_id"]

    llm.queue(text_response("Published."))
    llm.queue(verdict_reply())
    await client.post(f"/api/approvals/{approval_id}/approve", json={})

    again = await client.post(f"/api/approvals/{approval_id}/approve", json={})
    assert again.status_code == 409


async def test_the_approval_payload_carries_no_secrets(
    client: AsyncClient, llm: ScriptedLLMClient, registry
) -> None:
    class SecretTool(Tool):
        name = "secret_deploy"
        description = "Deploy using a token."
        permission = PermissionLevel.CRITICAL
        input_schema = {
            "type": "object",
            "properties": {"api_key": {"type": "string"}},
            "additionalProperties": False,
        }

        async def execute(self, arguments, context) -> ToolResult:
            return ToolResult.success("deployed")

    registry.register(SecretTool())
    llm.queue(plan_reply(("Deploy", "secret_deploy", "deployed")))
    llm.queue(tool_response("secret_deploy", {"api_key": "sk-ant-0123456789abcdef"}))

    paused = (
        await client.post("/api/agent/run", json={"message": "Deploy it."})
    ).json()

    assert paused["approval"]["tool_arguments"]["api_key"] == "***REDACTED***"


async def test_a_missing_api_key_is_a_clear_503(client: AsyncClient) -> None:
    """The service stays up; only the agent route reports the missing key."""
    client._transport.app.state.llm = None  # type: ignore[attr-defined]

    response = await client.post("/api/agent/run", json={"message": "hi"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "configuration_error"
    assert "ANTHROPIC_API_KEY" in response.json()["error"]["message"]
    assert (await client.get("/api/health")).status_code == 200
