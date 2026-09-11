"""Tools that let the agent read and write its own long-term memory.

These are the first real example of a tool that needs the run's database
session: both reach it through ``ToolContext.session``, which is exactly why the
context object exists.
"""

from __future__ import annotations

import uuid
from typing import Any

from ulugbek_ai.core.enums import MemoryType, PermissionLevel
from ulugbek_ai.memory.manager import MemoryManager
from ulugbek_ai.tools.base import Tool, ToolContext, ToolResult, ToolVerification

_MEMORY_TYPE_VALUES = [member.value for member in MemoryType]


class MemorySearchTool(Tool):
    name = "memory_search"
    description = (
        "Search the agent's long-term memory for facts, decisions and user "
        "preferences relevant to a query. Use this before asking the user "
        "something they may have already told you. Read-only."
    )
    permission = PermissionLevel.READ
    timeout_seconds = 15.0
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to look for.",
                "minLength": 1,
                "maxLength": 2_000,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of memories to return.",
                "minimum": 1,
                "maximum": 25,
            },
            "types": {
                "type": "array",
                "description": "Restrict the search to these memory types.",
                "items": {"type": "string", "enum": _MEMORY_TYPE_VALUES},
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "count": {"type": "integer"},
            "memories": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["query", "count", "memories"],
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        if context.session is None:
            return ToolResult.failure("memory_search requires a database session.")

        types = [MemoryType(value) for value in arguments.get("types", [])] or None
        manager = MemoryManager(context.session)
        results = await manager.search_memory(
            arguments["query"],
            user_id=context.user_id,
            project_id=context.project_id,
            types=types,
            limit=int(arguments.get("limit", 5)),
        )
        memories = [
            {
                "id": str(item.memory.id),
                "type": str(item.memory.type),
                "content": item.memory.content,
                "score": round(item.score, 3),
                "created_at": item.memory.created_at.isoformat()
                if item.memory.created_at
                else None,
            }
            for item in results
        ]
        return ToolResult.success(
            {
                "query": arguments["query"],
                "count": len(memories),
                "memories": memories,
            },
            count=len(memories),
        )


class MemoryWriteTool(Tool):
    name = "memory_write"
    description = (
        "Store a durable fact, decision or user preference in long-term memory "
        "so future runs can use it. Write one self-contained statement — not a "
        "transcript. Do not store secrets, API keys or passwords."
    )
    permission = PermissionLevel.WRITE
    timeout_seconds = 15.0
    idempotent = False
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact to remember, stated in full.",
                "minLength": 3,
                "maxLength": 5_000,
            },
            "type": {
                "type": "string",
                "description": "Which kind of memory this is.",
                "enum": _MEMORY_TYPE_VALUES,
            },
            "importance": {
                "type": "number",
                "description": "0 = trivia, 1 = essential.",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "tags": {
                "type": "array",
                "items": {"type": "string", "maxLength": 50},
                "maxItems": 10,
            },
        },
        "required": ["content", "type"],
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"id": {"type": "string"}, "type": {"type": "string"}},
        "required": ["id", "type"],
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        if context.session is None:
            return ToolResult.failure("memory_write requires a database session.")

        manager = MemoryManager(context.session)
        memory = await manager.remember(
            arguments["content"],
            type=MemoryType(arguments["type"]),
            user_id=context.user_id,
            project_id=context.project_id,
            task_id=context.task_id,
            importance=float(arguments.get("importance", 0.5)),
            source="agent.memory_write",
            tags=arguments.get("tags", []),
        )
        return ToolResult.success(
            {"id": str(memory.id), "type": str(memory.type)},
            memory_id=str(memory.id),
        )

    async def verify(
        self,
        arguments: dict[str, Any],
        result: ToolResult,
        context: ToolContext,
    ) -> ToolVerification:
        """Re-read the row to prove the write actually landed."""
        if not result.ok:
            return ToolVerification.failure(result.error or "write failed")
        if context.session is None:
            return ToolVerification.skipped("no session available to re-read")

        memory_id = result.evidence.get("memory_id")
        if not memory_id:
            return ToolVerification.failure("tool returned no memory id")

        manager = MemoryManager(context.session)
        stored = await manager.get_memory(uuid.UUID(memory_id))
        if stored is None:  # pragma: no cover - get_memory raises instead
            return ToolVerification.failure(f"memory {memory_id} not found after write")
        return ToolVerification.success(f"memory {memory_id} is readable after write")
