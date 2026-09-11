"""Current time — the canonical minimal READ tool."""

from __future__ import annotations

from datetime import timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ulugbek_ai.core.enums import PermissionLevel
from ulugbek_ai.core.utils import utcnow
from ulugbek_ai.tools.base import Tool, ToolContext, ToolResult


class ClockTool(Tool):
    name = "current_time"
    description = (
        "Return the current date and time. Use this whenever the answer depends "
        "on 'now' — never guess the date. Optionally pass an IANA timezone name "
        "such as 'Asia/Tashkent'."
    )
    permission = PermissionLevel.READ
    timeout_seconds = 5.0
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "IANA timezone name. Defaults to UTC.",
            }
        },
        "additionalProperties": False,
    }
    output_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "iso": {"type": "string"},
            "timezone": {"type": "string"},
            "unix": {"type": "number"},
        },
        "required": ["iso", "timezone", "unix"],
    }

    async def execute(
        self, arguments: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        name = arguments.get("timezone") or "UTC"
        try:
            tzinfo = timezone.utc if name.upper() == "UTC" else ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return ToolResult.failure(f"Unknown timezone: {name!r}")

        now = utcnow().astimezone(tzinfo)
        return ToolResult.success(
            {"iso": now.isoformat(), "timezone": name, "unix": now.timestamp()},
            timezone=name,
        )
