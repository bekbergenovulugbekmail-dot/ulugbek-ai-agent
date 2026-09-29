"""The project registry, as the agent sees it.

Reading it, and adding to it. The second matters more than it looks: a project
is what carries a repository, and without one the GitHub tools have nothing to
address and refuse every call. Before this existed the only way to register one
was an HTTP request the operator had to compose themselves, which is a strange
thing to ask of someone who is already talking to the agent.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Final

from ulugbek_ai.core.enums import PermissionLevel, ProjectStatus
from ulugbek_ai.core.errors import ConflictError
from ulugbek_ai.projects.manager import ProjectManager
from ulugbek_ai.projects.repository import ProjectRepository
from ulugbek_ai.projects.schemas import ProjectCreate
from ulugbek_ai.tools.base import Tool, ToolContext, ToolResult, ToolVerification

#: `owner/name`, which is the only shape the GitHub tools can address. Storing
#: anything else produces a project that looks configured and fails at every
#: call afterwards, a long way from the typo.
_REPOSITORY_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$"
)


class ProjectListTool(Tool):
    name = "project_list"
    description = (
        "List the projects this agent knows about, with their status, "
        "repository and configured integrations. Use it to find out which "
        "project a request belongs to. Read-only."
    )
    permission = PermissionLevel.READ
    timeout_seconds = 15.0
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "description": "Only projects in this status.",
                "enum": [member.value for member in ProjectStatus],
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
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

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        if context.session is None:
            return ToolResult.failure("project_list requires a database session.")

        status = arguments.get("status")
        repository = ProjectRepository(context.session)
        projects = await repository.list(
            status=ProjectStatus(status) if status else None,
            limit=int(arguments.get("limit", 25)),
        )
        payload = [
            {
                "id": str(project.id),
                "name": project.name,
                "slug": project.slug,
                "status": str(project.status),
                "description": project.description,
                "repository": project.repository,
                "environment": project.environment,
                "integrations": sorted(project.integrations or {}),
            }
            for project in projects
        ]
        return ToolResult.success(
            {"count": len(payload), "projects": payload}, count=len(payload)
        )


class ProjectCreateTool(Tool):
    name = "project_create"
    description = (
        "Register a new project so the agent can work on it. The repository, "
        "given as `owner/name`, is what lets the GitHub tools address it "
        "without being told the name every time. Ask the user before inventing "
        "a project: creating one is cheap, but a duplicate is confusing."
    )
    permission = PermissionLevel.WRITE
    timeout_seconds = 15.0
    #: It creates a row. Running it twice is a second project, not the same one.
    idempotent = False
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "What the project is called.",
                "minLength": 1,
                "maxLength": 200,
            },
            "description": {
                "type": "string",
                "description": "What it is, in a sentence or two.",
                "maxLength": 10_000,
            },
            "repository": {
                "type": "string",
                "description": "The GitHub repository as `owner/name`.",
                "maxLength": 500,
            },
            "environment": {
                "type": "string",
                "description": "Deployment environment, e.g. production.",
                "maxLength": 100,
            },
            "keywords": {
                "type": "array",
                "description": (
                    "Words that should route a request to this project."
                ),
                "items": {"type": "string", "maxLength": 50},
                "maxItems": 20,
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "slug": {"type": "string"},
            "repository": {"type": ["string", "null"]},
        },
        "required": ["id", "name", "slug"],
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        if context.session is None:
            return ToolResult.failure("project_create requires a database session.")

        repository = (arguments.get("repository") or "").strip() or None
        if repository is not None and not _REPOSITORY_RE.match(repository):
            return ToolResult.failure(
                f"'{repository}' is not a repository this agent can address. "
                "Give it as owner/name, with no host and no trailing path."
            )

        manager = ProjectManager(context.session)
        try:
            project = await manager.create(
                ProjectCreate(
                    name=arguments["name"],
                    description=arguments.get("description"),
                    repository=repository,
                    environment=arguments.get("environment"),
                    keywords=arguments.get("keywords", []),
                ),
                owner_id=context.user_id,
            )
        except ConflictError as exc:
            # The slug collided. Said plainly, because the model reads this and
            # has to decide between renaming and using the project that exists.
            return ToolResult.failure(str(exc))

        return ToolResult.success(
            {
                "id": str(project.id),
                "name": project.name,
                "slug": project.slug,
                "repository": project.repository,
            },
            project_id=str(project.id),
        )

    async def summarize(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> str | None:
        name = arguments.get("name", "a project")
        repository = arguments.get("repository")
        return (
            f"Register the project {name!r}"
            + (f", bound to the repository {repository}" if repository else "")
            + "."
        )

    async def verify(
        self,
        arguments: dict[str, Any],
        result: ToolResult,
        context: ToolContext,
    ) -> ToolVerification:
        """Re-read the row, rather than trusting that the insert landed."""
        if not result.ok:
            return ToolVerification.failure(result.error or "creation failed")
        if context.session is None:  # pragma: no cover - execute already refused
            return ToolVerification.skipped("no session available to re-read")

        project_id = result.evidence.get("project_id")
        if not project_id:  # pragma: no cover - success always carries one
            return ToolVerification.failure("tool returned no project id")

        stored = await ProjectRepository(context.session).get(uuid.UUID(project_id))
        if stored is None:
            return ToolVerification.failure(
                f"project {project_id} is not readable after creation"
            )
        return ToolVerification.success(
            f"project {stored.slug} is readable after creation"
        )
