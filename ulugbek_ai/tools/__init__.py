"""Universal tool system.

A tool is the agent's only way to affect the outside world. Every tool declares
a name, description, input/output schema, permission level and timeout; the
registry enforces those uniformly, so adding a GitHub, Railway, Telegram,
Instagram or ERP adapter in a later phase means writing one :class:`Tool`
subclass — no changes to the engine, the permission system or the API.
"""

from ulugbek_ai.tools.base import Tool, ToolContext, ToolResult
from ulugbek_ai.tools.permissions import (
    PermissionDecision,
    PermissionPolicy,
    PermissionService,
)
from ulugbek_ai.tools.registry import ToolRegistry, build_default_registry

__all__ = [
    "PermissionDecision",
    "PermissionPolicy",
    "PermissionService",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "build_default_registry",
]
