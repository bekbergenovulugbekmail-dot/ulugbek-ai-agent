"""Built-in tools.

Phase 1 deliberately ships only local, side-effect-free tools. They exist to
exercise the registry, the permission system and the agent loop end to end —
and they are genuinely useful to the agent (reading its own memory and the
project list). External integrations arrive in later phases as additional
:class:`~ulugbek_ai.tools.base.Tool` subclasses.
"""

from ulugbek_ai.tools.base import Tool
from ulugbek_ai.tools.builtin.calculator import CalculatorTool
from ulugbek_ai.tools.builtin.clock import ClockTool
from ulugbek_ai.tools.builtin.memory_tools import MemorySearchTool, MemoryWriteTool
from ulugbek_ai.tools.builtin.projects import ProjectListTool

__all__ = [
    "CalculatorTool",
    "ClockTool",
    "MemorySearchTool",
    "MemoryWriteTool",
    "ProjectListTool",
    "default_tools",
]


def default_tools() -> list[Tool]:
    """Instances registered by :func:`~ulugbek_ai.tools.registry.build_default_registry`."""
    return [
        ClockTool(),
        CalculatorTool(),
        MemorySearchTool(),
        MemoryWriteTool(),
        ProjectListTool(),
    ]
