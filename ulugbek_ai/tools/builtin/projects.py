"""Read-only view of the project registry."""

from __future__ import annotations

from typing import Any

from ulugbek_ai.core.enums import PermissionLevel, ProjectStatus
from ulugbek_ai.projects.repository import ProjectRepository
from ulugbek_ai.tools.base import Tool, ToolContext, ToolResult


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
