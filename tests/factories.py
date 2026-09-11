"""Helpers for building scripted LLM replies in tests."""

from __future__ import annotations

import json
from typing import Any

from ulugbek_ai.llm.scripted import text_response, tool_response

__all__ = ["plan_reply", "text_response", "tool_response", "verdict_reply"]


def plan_reply(
    *steps: tuple[str, str | None, str],
    goal: str = "Do the thing",
    requires_tools: bool = False,
) -> Any:
    """A planner reply. Each step is ``(description, tool, expected_outcome)``."""
    payload = {
        "goal_restatement": goal,
        "requires_tools": requires_tools,
        "reasoning": "test plan",
        "steps": [
            {"description": description, "tool": tool, "expected_outcome": expected}
            for description, tool, expected in steps
        ],
    }
    return text_response(json.dumps(payload))


def verdict_reply(
    status: str = "SUCCESS", reason: str = "looks right", missing: list[str] | None = None
) -> Any:
    """A verifier reply."""
    return text_response(
        json.dumps({"status": status, "reason": reason, "missing": missing or []})
    )
