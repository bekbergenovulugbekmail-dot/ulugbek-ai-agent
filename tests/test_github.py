"""GitHub integration: client, error translation, tools and verification.

The HTTP layer is a mock transport, so these run offline and deterministically.
A live check against the real API lives in ``scripts/smoke_github.py``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.enums import PermissionLevel
from ulugbek_ai.core.errors import ValidationError
from ulugbek_ai.integrations.github.client import GitHubApiError, GitHubClient, parse_repository
from ulugbek_ai.integrations.github.tools import (
    GitHubCommitsTool,
    GitHubCreateIssueTool,
    GitHubRepoInfoTool,
    GitHubWorkflowRunsTool,
    github_tools,
)
from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.schemas import ProjectCreate
from ulugbek_ai.tools.base import ToolContext
from ulugbek_ai.tools.registry import build_default_registry

TOKEN = "ghp_thisisasecrettokenvalue0123456789"


def transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def client_with(handler, **kwargs: Any) -> GitHubClient:
    return GitHubClient(transport=transport(handler), **kwargs)


def json_response(payload: Any, status: int = 200, headers=None) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers or {})


# --------------------------------------------------------------------------- #
# Repository references
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("owner/repo", "owner/repo"),
        ("  owner/repo  ", "owner/repo"),
        ("https://github.com/owner/repo", "owner/repo"),
        ("https://github.com/owner/repo.git", "owner/repo"),
        ("owner/repo.name-1", "owner/repo.name-1"),
    ],
)
def test_repository_references_are_normalized(value: str, expected: str) -> None:
    assert parse_repository(value) == expected


@pytest.mark.parametrize(
    "value", ["", "bad", "a/b/c", "/leading", "trailing/", "own er/repo", "-bad/repo"]
)
def test_malformed_repository_references_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        parse_repository(value)


# --------------------------------------------------------------------------- #
# Client: projection and headers
# --------------------------------------------------------------------------- #
async def test_repository_payload_is_projected_not_forwarded() -> None:
    """A GitHub payload is huge; only the fields the agent reasons about pass."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return json_response(
            {
                "full_name": "ulugbek/erp",
                "description": "Warehouse",
                "default_branch": "main",
                "private": False,
                "pushed_at": "2026-01-01T00:00:00Z",
                "html_url": "https://github.com/ulugbek/erp",
                "owner": {"login": "ulugbek", "avatar_url": "…", "id": 1},
                "permissions": {"admin": True},
                "network_count": 12,
            }
        )

    info = await client_with(handler).get_repository("ulugbek/erp")

    assert captured["url"].endswith("/repos/ulugbek/erp")
    assert info["default_branch"] == "main"
    # Noise the model has no use for is dropped.
    assert "owner" not in info
    assert "permissions" not in info
    assert "network_count" not in info


async def test_the_token_is_sent_as_a_header_and_nowhere_else() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["version"] = request.headers.get("x-github-api-version")
        return json_response({"full_name": "a/b"})

    await client_with(handler, token=TOKEN).get_repository("a/b")

    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["version"] == "2022-11-28"


async def test_no_authorization_header_without_a_token() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return json_response({"full_name": "a/b"})

    await client_with(handler).get_repository("a/b")
    assert seen["auth"] is None


async def test_commit_messages_are_reduced_to_a_subject_line() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            [
                {
                    "sha": "abcdef1234567890",
                    "html_url": "https://github.com/a/b/commit/abcdef",
                    "commit": {
                        "message": "Fix the thing\n\nA very long body…\n" + "x" * 5_000,
                        "author": {"name": "Ulugbek", "date": "2026-01-01T00:00:00Z"},
                    },
                }
            ]
        )

    commits = await client_with(handler).list_commits("a/b", limit=5)

    assert commits[0]["sha"] == "abcdef123456"
    assert commits[0]["message"] == "Fix the thing"
    assert len(commits[0]["message"]) < 100


async def test_workflow_runs_expose_the_conclusion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["branch"] == "main"
        return json_response(
            {
                "workflow_runs": [
                    {
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "success",
                        "head_branch": "main",
                        "head_sha": "abc123def456789",
                        "created_at": "2026-01-01T00:00:00Z",
                        "html_url": "https://github.com/a/b/actions/runs/1",
                    }
                ]
            }
        )

    runs = await client_with(handler).list_workflow_runs("a/b", branch="main")

    assert runs[0]["conclusion"] == "success"
    assert runs[0]["commit"] == "abc123def456"


# --------------------------------------------------------------------------- #
# Client: error translation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("status", "payload", "fragment", "retryable"),
    [
        (401, {"message": "Bad credentials"}, "rejected the credential", False),
        (404, {"message": "Not Found"}, "Not found on GitHub", False),
        (422, {"message": "Validation Failed"}, "rejected the request as invalid", False),
        (500, {"message": "oops"}, "having trouble", True),
        (503, {"message": "oops"}, "having trouble", True),
    ],
)
async def test_http_failures_become_readable_messages(
    status: int, payload: dict, fragment: str, retryable: bool
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(payload, status)

    with pytest.raises(GitHubApiError) as exc_info:
        await client_with(handler).get_repository("a/b")

    assert fragment in exc_info.value.message
    assert exc_info.value.status == status
    assert exc_info.value.retryable is retryable


async def test_rate_limiting_says_what_to_do_about_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {"message": "API rate limit exceeded"},
            403,
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-limit": "60",
                "x-ratelimit-reset": "1767225600",
            },
        )

    with pytest.raises(GitHubApiError) as exc_info:
        await client_with(handler).get_repository("a/b")

    assert "rate limit" in exc_info.value.message.lower()
    assert "GITHUB_TOKEN" in exc_info.value.message  # unauthenticated advice
    assert exc_info.value.retryable is True


async def test_an_error_message_never_contains_the_token() -> None:
    """The one failure mode that would be unforgivable."""

    def handler(request: httpx.Request) -> httpx.Response:
        # GitHub echoing the credential back would still not leak it.
        return json_response({"message": f"Bad credentials: {TOKEN}"}, 403)

    with pytest.raises(GitHubApiError) as exc_info:
        await client_with(handler, token=TOKEN).get_repository("a/b")

    assert TOKEN not in exc_info.value.message
    assert TOKEN not in str(exc_info.value.details)


async def test_a_timeout_is_reported_as_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(GitHubApiError) as exc_info:
        await client_with(handler, timeout_seconds=1).get_repository("a/b")

    assert exc_info.value.retryable is True
    assert "did not respond" in exc_info.value.message


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
async def test_repo_info_tool_returns_a_usable_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {"full_name": "ulugbek/erp", "default_branch": "main", "private": False}
        )

    tool = GitHubRepoInfoTool(client_with(handler))
    result = await tool.execute({"repository": "ulugbek/erp"}, ToolContext())

    assert result.ok
    assert result.output["default_branch"] == "main"
    assert result.evidence["repository"] == "ulugbek/erp"


async def test_a_github_failure_becomes_a_failed_result_not_an_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"message": "Not Found"}, 404)

    tool = GitHubRepoInfoTool(client_with(handler))
    result = await tool.execute({"repository": "ulugbek/missing"}, ToolContext())

    assert result.ok is False
    assert "Not found on GitHub" in result.error
    assert result.evidence["status"] == 404


async def test_the_repository_comes_from_the_project_binding(
    session: AsyncSession,
) -> None:
    """The point of the Project system: the operator never pastes a repo name."""
    project = await ProjectManager(session).create(
        ProjectCreate(
            name="Telegram Bot",
            integrations={"github": {"repository": "ulugbek/telegram-bot"}},
        )
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return json_response({"full_name": "ulugbek/telegram-bot"})

    tool = GitHubRepoInfoTool(client_with(handler))
    result = await tool.execute(
        {}, ToolContext(session=session, project_id=project.id)
    )

    assert result.ok
    assert seen["url"].endswith("/repos/ulugbek/telegram-bot")


async def test_the_plain_repository_field_is_used_when_there_is_no_binding(
    session: AsyncSession,
) -> None:
    project = await ProjectManager(session).create(
        ProjectCreate(name="ERP", repository="ulugbek/erp")
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return json_response({"full_name": "ulugbek/erp"})

    await GitHubRepoInfoTool(client_with(handler)).execute(
        {}, ToolContext(session=session, project_id=project.id)
    )
    assert seen["url"].endswith("/repos/ulugbek/erp")


async def test_an_explicit_repository_beats_the_project_binding(
    session: AsyncSession,
) -> None:
    project = await ProjectManager(session).create(
        ProjectCreate(name="ERP", repository="ulugbek/erp")
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return json_response({"full_name": "other/repo"})

    await GitHubRepoInfoTool(client_with(handler)).execute(
        {"repository": "other/repo"},
        ToolContext(session=session, project_id=project.id),
    )
    assert seen["url"].endswith("/repos/other/repo")


async def test_a_missing_repository_asks_for_one_instead_of_guessing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be made")

    result = await GitHubRepoInfoTool(client_with(handler)).execute({}, ToolContext())

    assert result.ok is False
    assert "owner/name" in result.error


async def test_commits_tool_passes_the_branch_through() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["sha"] == "develop"
        assert request.url.params["per_page"] == "3"
        return json_response([])

    result = await GitHubCommitsTool(client_with(handler)).execute(
        {"repository": "a/b", "branch": "develop", "limit": 3}, ToolContext()
    )
    assert result.ok
    assert result.output["count"] == 0


async def test_workflow_tool_surfaces_the_latest_conclusion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response(
            {
                "workflow_runs": [
                    {"name": "CI", "status": "completed", "conclusion": "failure"}
                ]
            }
        )

    result = await GitHubWorkflowRunsTool(client_with(handler)).execute(
        {"repository": "a/b"}, ToolContext()
    )

    assert result.output["latest_conclusion"] == "failure"
    assert result.evidence["latest_conclusion"] == "failure"


# --------------------------------------------------------------------------- #
# The write tool: permission and verification
# --------------------------------------------------------------------------- #
def test_opening_an_issue_is_gated_behind_approval() -> None:
    """Changing something other people can see is never automatic."""
    assert GitHubCreateIssueTool().permission is PermissionLevel.EXECUTE
    assert GitHubCreateIssueTool().idempotent is False


async def test_creating_an_issue_verifies_by_reading_it_back() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["title"] == "CI is failing on main"
            return json_response(
                {
                    "number": 42,
                    "title": body["title"],
                    "state": "open",
                    "html_url": "https://github.com/a/b/issues/42",
                },
                201,
            )
        return json_response(
            {
                "number": 42,
                "title": "CI is failing on main",
                "state": "open",
                "html_url": "https://github.com/a/b/issues/42",
            }
        )

    tool = GitHubCreateIssueTool(client_with(handler))
    arguments = {"repository": "a/b", "title": "CI is failing on main"}
    result = await tool.execute(arguments, ToolContext())
    verification = await tool.verify(arguments, result, ToolContext())

    assert result.ok
    assert result.output["number"] == 42
    assert verification.status.value == "SUCCESS"
    assert calls == ["POST /repos/a/b/issues", "GET /repos/a/b/issues/42"]


async def test_verification_fails_when_the_issue_cannot_be_read_back() -> None:
    """A create that GitHub accepted but that is not there must not pass."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return json_response(
                {"number": 7, "title": "t", "html_url": "u"}, 201
            )
        return json_response({"message": "Not Found"}, 404)

    tool = GitHubCreateIssueTool(client_with(handler))
    arguments = {"repository": "a/b", "title": "title"}
    result = await tool.execute(arguments, ToolContext())
    verification = await tool.verify(arguments, result, ToolContext())

    assert result.ok
    assert verification.status.value == "FAILURE"
    assert "#7" in verification.reason


# --------------------------------------------------------------------------- #
# Registry wiring
# --------------------------------------------------------------------------- #
def test_github_tools_are_registered_from_settings() -> None:
    registry = build_default_registry(settings=Settings(_env_file=None))
    names = {tool.name for tool in registry.list()}

    assert {
        "github_repo_info",
        "github_list_commits",
        "github_workflow_runs",
        "github_list_pull_requests",
        "github_create_issue",
    } <= names
    assert registry.get("github_repo_info").service == "github"
    # Local tools are still there.
    assert "calculate" in names


def test_a_registry_without_settings_stays_local_only() -> None:
    names = {tool.name for tool in build_default_registry().list()}
    assert not any(name.startswith("github_") for name in names)


def test_every_github_tool_declares_its_service() -> None:
    assert all(tool.service == "github" for tool in github_tools())


def test_the_tool_description_reaches_the_model() -> None:
    spec = GitHubRepoInfoTool().to_llm_spec()
    assert "default branch" in spec.description
    assert spec.input_schema["additionalProperties"] is False


# --------------------------------------------------------------------------- #
# A 404 on a branch has to say which of the two things is wrong
# --------------------------------------------------------------------------- #
async def test_a_missing_branch_names_the_default_instead_of_saying_not_found() -> None:
    """A dead-end error makes an agent retry the same wrong branch forever.

    This is the failure that broke the first live GitHub run: the model guessed
    `main`, the repository used a different default, and "not found" told it
    nothing it could act on.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/commits"):
            return json_response({"message": "Not Found"}, 404)
        return json_response(
            {"full_name": "ulugbek/erp", "default_branch": "develop"}
        )

    result = await GitHubCommitsTool(client_with(handler)).execute(
        {"repository": "ulugbek/erp", "branch": "main"}, ToolContext()
    )

    assert result.ok is False
    assert "Branch 'main' does not exist" in result.error
    assert "develop" in result.error
    assert "Retry without the branch argument" in result.error


async def test_a_missing_repository_still_reads_as_a_repository_problem() -> None:
    """The same 404 must not be blamed on the branch when the repo is gone."""
    def handler(request: httpx.Request) -> httpx.Response:
        return json_response({"message": "Not Found"}, 404)

    result = await GitHubCommitsTool(client_with(handler)).execute(
        {"repository": "ulugbek/missing", "branch": "main"}, ToolContext()
    )

    assert result.ok is False
    assert "Repository ulugbek/missing could not be read" in result.error
    assert "Branch" not in result.error


async def test_no_extra_request_when_no_branch_was_given() -> None:
    """The diagnosis costs a request, so it only runs when a branch is in play."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return json_response({"message": "Not Found"}, 404)

    result = await GitHubCommitsTool(client_with(handler)).execute(
        {"repository": "ulugbek/erp"}, ToolContext()
    )

    assert result.ok is False
    assert calls == ["/repos/ulugbek/erp/commits"]


def test_the_branch_argument_tells_the_model_not_to_guess() -> None:
    schema = GitHubCommitsTool().input_schema["properties"]["branch"]
    assert "do not guess" in schema["description"].lower()
