"""GitHub tools.

The read tools let the agent inspect a repository — the state of a branch, what
landed recently, whether CI is green, what is waiting in review. The one write
tool opens an issue, and it is classified ``EXECUTE`` rather than ``WRITE``:
anything that changes an external service other people can see should stop and
ask a human first, whatever the policy says about local writes.

Every tool resolves the repository the same way, so the operator rarely has to
name it: an explicit argument wins, otherwise the project's GitHub binding is
used. That binding is the seam the Project system was built for.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any

from pydantic import SecretStr

from ulugbek_ai.core.enums import PermissionLevel
from ulugbek_ai.core.errors import ValidationError
from ulugbek_ai.integrations.github.client import GitHubApiError, GitHubClient
from ulugbek_ai.projects.repository import ProjectRepository
from ulugbek_ai.tools.base import Tool, ToolContext, ToolResult, ToolVerification

logger = logging.getLogger(__name__)

SERVICE = "github"

_REPOSITORY_PROPERTY: dict[str, Any] = {
    "type": "string",
    "description": (
        "Repository as owner/name. Omit it to use the repository configured on "
        "the current project."
    ),
}


class GitHubTool(Tool):
    """Shared behaviour for every GitHub tool."""

    service = SERVICE
    timeout_seconds = 30.0

    def __init__(self, client: GitHubClient | None = None) -> None:
        self._client = client or GitHubClient()

    @property
    def client(self) -> GitHubClient:
        return self._client

    async def resolve_repository(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> str:
        """Explicit argument, else the current project's GitHub binding."""
        explicit = arguments.get("repository")
        if explicit:
            return str(explicit)

        if context.session is not None and context.project_id is not None:
            project = await ProjectRepository(context.session).get(context.project_id)
            if project is not None:
                binding = (project.integrations or {}).get(SERVICE) or {}
                candidate = binding.get("repository") or project.repository
                if candidate:
                    return str(candidate)

        raise ValidationError(
            "No repository given and the current project has no GitHub "
            "repository configured. Pass repository as owner/name.",
        )

    @abstractmethod
    async def run(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        """Do the work. Subclasses implement this instead of ``execute``."""

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        """Translate GitHub and validation failures into a readable result.

        The registry would already catch an exception, but a bare
        ``GitHubApiError: ...`` is not something to hand a user — this produces
        the message a person can act on, and keeps the retry hint.
        """
        try:
            return await self.run(arguments, context)
        except ValidationError as exc:
            return ToolResult.failure(exc.message)
        except GitHubApiError as exc:
            return ToolResult.failure(
                exc.message, status=exc.status, retryable=exc.retryable
            )


class GitHubRepoInfoTool(GitHubTool):
    name = "github_repo_info"
    description = (
        "Read a GitHub repository's current state: default branch, visibility, "
        "language, open issue count and when it was last pushed to. Use this "
        "first when asked about the state of a project on GitHub."
    )
    permission = PermissionLevel.READ
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"repository": _REPOSITORY_PROPERTY},
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "repository": {"type": "string"},
            "default_branch": {"type": "string"},
            "pushed_at": {"type": "string"},
        },
        "required": ["repository"],
    }

    async def run(self, arguments, context) -> ToolResult:
        repository = await self.resolve_repository(arguments, context)
        info = await self.client.get_repository(repository)
        return ToolResult.success(info, repository=info["repository"])


class GitHubCommitsTool(GitHubTool):
    name = "github_list_commits"
    description = (
        "List recent commits on a GitHub branch, newest first. Use it to see "
        "what has changed lately, or to check whether specific work landed."
    )
    permission = PermissionLevel.READ
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "repository": _REPOSITORY_PROPERTY,
            "branch": {
                "type": "string",
                "description": (
                    "Branch name. Omit this to use the repository's default "
                    "branch — do not guess 'main' or 'master', many "
                    "repositories use neither."
                ),
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 30},
        },
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "repository": {"type": "string"},
            "count": {"type": "integer"},
            "commits": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["repository", "count", "commits"],
    }

    async def run(self, arguments, context) -> ToolResult:
        repository = await self.resolve_repository(arguments, context)
        commits = await self.client.list_commits(
            repository,
            branch=arguments.get("branch"),
            limit=int(arguments.get("limit", 10)),
        )
        return ToolResult.success(
            {
                "repository": repository,
                "branch": arguments.get("branch"),
                "count": len(commits),
                "commits": commits,
            },
            count=len(commits),
        )


class GitHubWorkflowRunsTool(GitHubTool):
    name = "github_workflow_runs"
    description = (
        "List recent GitHub Actions runs for a repository, with their status "
        "and conclusion. Use it to answer whether CI is green or a deployment "
        "workflow succeeded — do not infer that from commits alone."
    )
    permission = PermissionLevel.READ
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "repository": _REPOSITORY_PROPERTY,
            "branch": {"type": "string", "description": "Restrict to this branch."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "repository": {"type": "string"},
            "count": {"type": "integer"},
            "runs": {"type": "array", "items": {"type": "object"}},
            "latest_conclusion": {"type": ["string", "null"]},
        },
        "required": ["repository", "count", "runs"],
    }

    async def run(self, arguments, context) -> ToolResult:
        repository = await self.resolve_repository(arguments, context)
        runs = await self.client.list_workflow_runs(
            repository,
            branch=arguments.get("branch"),
            limit=int(arguments.get("limit", 5)),
        )
        latest = runs[0]["conclusion"] if runs else None
        return ToolResult.success(
            {
                "repository": repository,
                "count": len(runs),
                "runs": runs,
                "latest_conclusion": latest,
            },
            count=len(runs),
            latest_conclusion=latest,
        )


class GitHubPullRequestsTool(GitHubTool):
    name = "github_list_pull_requests"
    description = (
        "List pull requests on a GitHub repository. Use it to see what is "
        "waiting for review or what was recently merged."
    )
    permission = PermissionLevel.READ
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "repository": _REPOSITORY_PROPERTY,
            "state": {"type": "string", "enum": ["open", "closed", "all"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 30},
        },
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "repository": {"type": "string"},
            "count": {"type": "integer"},
            "pull_requests": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["repository", "count", "pull_requests"],
    }

    async def run(self, arguments, context) -> ToolResult:
        repository = await self.resolve_repository(arguments, context)
        pulls = await self.client.list_pull_requests(
            repository,
            state=str(arguments.get("state", "open")),
            limit=int(arguments.get("limit", 10)),
        )
        return ToolResult.success(
            {
                "repository": repository,
                "state": arguments.get("state", "open"),
                "count": len(pulls),
                "pull_requests": pulls,
            },
            count=len(pulls),
        )


class GitHubCreateIssueTool(GitHubTool):
    """Open an issue — the first tool that changes something other people see.

    Classified ``EXECUTE`` so it stops for approval by default, and it verifies
    its own effect by reading the issue back rather than trusting the response.
    """

    name = "github_create_issue"
    description = (
        "Open an issue on a GitHub repository. Use this only when the user "
        "asked for an issue to be created. It requires human approval."
    )
    permission = PermissionLevel.EXECUTE
    idempotent = False
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "repository": _REPOSITORY_PROPERTY,
            "title": {"type": "string", "minLength": 3, "maxLength": 250},
            "body": {"type": "string", "maxLength": 5_000},
            "labels": {
                "type": "array",
                "items": {"type": "string", "maxLength": 50},
                "maxItems": 10,
            },
        },
        "required": ["title"],
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "number": {"type": "integer"},
            "title": {"type": "string"},
            "url": {"type": "string"},
        },
        "required": ["number", "url"],
    }

    async def run(self, arguments, context) -> ToolResult:
        repository = await self.resolve_repository(arguments, context)
        issue = await self.client.create_issue(
            repository,
            title=str(arguments["title"]),
            body=arguments.get("body"),
            labels=arguments.get("labels"),
        )
        return ToolResult.success(
            {"repository": repository, **issue},
            repository=repository,
            issue_number=issue.get("number"),
        )

    async def verify(
        self,
        arguments: dict[str, Any],
        result: ToolResult,
        context: ToolContext,
    ) -> ToolVerification:
        """Read the issue back, so "created" means it is actually there."""
        if not result.ok:
            return ToolVerification.failure(result.error or "issue was not created")

        repository = result.evidence.get("repository")
        number = result.evidence.get("issue_number")
        if not repository or not number:
            return ToolVerification.failure(
                "GitHub did not return an issue number, so the write cannot be "
                "confirmed."
            )

        try:
            issue = await self.client.get_issue(str(repository), int(number))
        except GitHubApiError as exc:
            return ToolVerification.failure(
                f"Could not confirm issue #{number} exists: {exc.message}"
            )

        if issue.get("number") != int(number):
            return ToolVerification.failure(
                f"Issue #{number} could not be read back from {repository}."
            )
        return ToolVerification.success(
            f"issue #{number} is readable at {issue.get('url')}"
        )


def github_tools(
    *,
    token: SecretStr | str | None = None,
    api_url: str | None = None,
    timeout_seconds: float = 20.0,
    client: GitHubClient | None = None,
) -> list[Tool]:
    """Every GitHub tool, sharing one client configuration."""
    shared = client or GitHubClient(
        token=token,
        api_url=api_url or "https://api.github.com",
        timeout_seconds=timeout_seconds,
    )
    return [
        GitHubRepoInfoTool(shared),
        GitHubCommitsTool(shared),
        GitHubWorkflowRunsTool(shared),
        GitHubPullRequestsTool(shared),
        GitHubCreateIssueTool(shared),
    ]
