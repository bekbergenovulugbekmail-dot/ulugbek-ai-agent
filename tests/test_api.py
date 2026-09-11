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


# --------------------------------------------------------------------------- #
# Control-centre endpoints (Phase 2)
# --------------------------------------------------------------------------- #
async def test_system_overview_feeds_the_dashboard(
    client: AsyncClient, llm: ScriptedLLMClient
) -> None:
    await client.post("/api/projects", json={"name": "ERP"})
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("done"))
    llm.queue(verdict_reply())
    await client.post("/api/agent/run", json={"message": "hi"})

    body = (await client.get("/api/system/overview")).json()

    assert body["counters"]["projects"] == 1
    assert body["counters"]["completed_tasks"] == 1
    assert body["counters"]["pending_approvals"] == 0
    assert body["agent"]["phase"] == "COMPLETED"
    assert {c["name"] for c in body["components"]} == {
        "API",
        "Database",
        "Claude",
        "Tool Registry",
    }
    assert body["tasks_by_status"]["COMPLETED"] == 1


async def test_overview_never_exposes_the_api_key(client: AsyncClient) -> None:
    body = (await client.get("/api/system/overview")).json()
    claude = next(c for c in body["components"] if c["name"] == "Claude")

    assert claude["status"] == "unconfigured"
    assert "sk-" not in str(body)


async def test_run_events_endpoint_returns_a_timeline(
    client: AsyncClient, llm: ScriptedLLMClient
) -> None:
    llm.queue(plan_reply(("Compute", "calculate", "a number")))
    llm.queue(tool_response("calculate", {"expression": "2+2"}))
    llm.queue(text_response("4"))
    llm.queue(verdict_reply())
    run_id = (
        await client.post("/api/agent/run", json={"message": "2+2?"})
    ).json()["run_id"]

    body = (await client.get(f"/api/events/runs/{run_id}")).json()
    types = [event["type"] for event in body["events"]]

    assert "agent.started" in types
    assert "tool.completed" in types
    assert "task.completed" in types
    assert body["cursor"] is not None


async def test_global_activity_feed(client: AsyncClient, llm: ScriptedLLMClient) -> None:
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("done"))
    llm.queue(verdict_reply())
    await client.post("/api/agent/run", json={"message": "hi"})

    body = (await client.get("/api/events", params={"limit": 5})).json()

    assert body["events"]
    assert all(event["safe_message"] for event in body["events"])


async def test_agent_state_endpoint(client: AsyncClient) -> None:
    body = (await client.get("/api/events/state")).json()

    assert body["phase"] == "IDLE"
    assert body["busy"] is False
    assert body["label"] == "Idle"


async def test_tool_execution_history(client: AsyncClient, llm: ScriptedLLMClient) -> None:
    llm.queue(plan_reply(("Compute", "calculate", "a number")))
    llm.queue(tool_response("calculate", {"expression": "6*7"}))
    llm.queue(text_response("42"))
    llm.queue(verdict_reply())
    await client.post("/api/agent/run", json={"message": "6*7?"})

    body = (await client.get("/api/tools/executions")).json()

    assert len(body) == 1
    assert body[0]["tool_name"] == "calculate"
    assert body[0]["status"] == "SUCCESS"
    assert body[0]["permission"] == "READ"


async def test_tool_execution_arguments_are_redacted(
    client: AsyncClient, llm: ScriptedLLMClient, registry
) -> None:
    class TokenTool(Tool):
        name = "token_tool"
        description = "Takes a token."
        permission = PermissionLevel.READ
        input_schema = {
            "type": "object",
            "properties": {"api_key": {"type": "string"}},
            "additionalProperties": False,
        }

        async def execute(self, arguments, context) -> ToolResult:
            return ToolResult.success("ok")

    registry.register(TokenTool())
    llm.queue(plan_reply(("Use it", "token_tool", "ok")))
    llm.queue(tool_response("token_tool", {"api_key": "sk-ant-0123456789abcdef"}))
    llm.queue(text_response("done"))
    llm.queue(verdict_reply())
    await client.post("/api/agent/run", json={"message": "use the token"})

    body = (await client.get("/api/tools/executions")).json()

    assert body[0]["arguments"]["api_key"] == "***REDACTED***"


async def test_project_overview_is_one_request(
    client: AsyncClient, llm: ScriptedLLMClient
) -> None:
    project_id = (
        await client.post(
            "/api/projects", json={"name": "ERP", "keywords": ["invoice"]}
        )
    ).json()["id"]
    await client.post(
        "/api/memory",
        json={
            "type": MemoryType.PROJECT_CONTEXT.value,
            "content": "Invoices start at 1000.",
            "project_id": project_id,
        },
    )
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("They start at 1000."))
    llm.queue(verdict_reply())
    await client.post("/api/agent/run", json={"message": "invoice numbering in ERP?"})

    body = (await client.get(f"/api/projects/{project_id}/overview")).json()

    assert body["project"]["slug"] == "erp"
    assert len(body["tasks"]) == 1
    assert body["memories"]
    assert body["activity"]


async def test_async_run_returns_immediately_then_completes(
    client: AsyncClient, llm: ScriptedLLMClient
) -> None:
    """The endpoint a UI uses: run id now, activity streamed after."""
    import asyncio

    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("Tashkent."))
    llm.queue(verdict_reply())

    started = await client.post("/api/agent/runs", json={"message": "capital?"})
    body = started.json()

    assert started.status_code == 202
    assert body["status"] == "RUNNING"
    assert body["output"] is None

    runner = client._transport.app.state.runner  # type: ignore[attr-defined]
    for _ in range(200):
        if runner.active_count == 0:
            break
        await asyncio.sleep(0.02)

    finished = (await client.get(f"/api/agent/runs/{body['run_id']}")).json()
    assert finished["status"] == "COMPLETED"
    assert finished["output"] == "Tashkent."


async def test_event_stream_emits_frames(
    client: AsyncClient, llm: ScriptedLLMClient
) -> None:
    """SSE: the transport a live timeline uses."""
    llm.queue(plan_reply(("Answer", None, "answered")))
    llm.queue(text_response("done"))
    llm.queue(verdict_reply())
    run_id = (
        await client.post("/api/agent/run", json={"message": "hi"})
    ).json()["run_id"]

    frames = ""
    async with client.stream(
        "GET", f"/api/events/runs/{run_id}/stream"
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for chunk in response.aiter_text():
            frames += chunk
            if "event: done" in frames:
                break

    assert "event: agent-event" in frames
    assert "event: agent-state" in frames
    assert '"type": "agent.started"' in frames
