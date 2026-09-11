"""Planning and replanning.

The planner asks the model for a structured plan using a JSON schema, so the
result is machine-checkable rather than prose the executor has to guess at. A
malformed reply is a hard error, never a silently empty plan.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ulugbek_ai.agent.prompts import PLANNER_SYSTEM, REPLANNER_SYSTEM
from ulugbek_ai.core.errors import LLMResponseFormatError
from ulugbek_ai.llm.base import LLMClient, LLMMessage
from ulugbek_ai.tasks.schemas import Plan, PlanStep
from ulugbek_ai.tools.base import Tool

logger = logging.getLogger(__name__)

MAX_PLAN_STEPS = 10

#: Schema the model's plan must satisfy.
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "goal_restatement": {
            "type": "string",
            "description": "The goal in your own words, one sentence.",
        },
        "requires_tools": {
            "type": "boolean",
            "description": "True if any step needs a tool.",
        },
        "reasoning": {
            "type": "string",
            "description": "Why this plan; keep it brief.",
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_PLAN_STEPS,
            "items": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What to do in this step.",
                    },
                    "tool": {
                        "type": ["string", "null"],
                        "description": "Tool name from the available list, or null.",
                    },
                    "expected_outcome": {
                        "type": "string",
                        "description": "What must be true when this step succeeded.",
                    },
                },
                "required": ["description", "tool", "expected_outcome"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["goal_restatement", "requires_tools", "reasoning", "steps"],
    "additionalProperties": False,
}


class Planner:
    """Turns a goal into a checkable :class:`~ulugbek_ai.tasks.schemas.Plan`."""

    def __init__(self, llm: LLMClient, *, max_steps: int = MAX_PLAN_STEPS) -> None:
        self._llm = llm
        self._max_steps = max_steps

    async def plan(
        self,
        goal: str,
        *,
        tools: list[Tool],
        context: str = "",
    ) -> Plan:
        """Produce an initial plan for *goal*."""
        prompt = self._build_prompt(goal, tools=tools, context=context)
        return await self._request(PLANNER_SYSTEM, prompt)

    async def replan(
        self,
        goal: str,
        *,
        tools: list[Tool],
        previous_plan: Plan,
        failure_reason: str,
        observations: list[str],
        context: str = "",
    ) -> Plan:
        """Produce a corrected plan after a failed attempt."""
        previous = "\n".join(
            f"{step.index + 1}. [{step.status}] {step.description}"
            for step in previous_plan.steps
        )
        observed = "\n".join(f"- {item}" for item in observations[-10:])
        prompt = self._build_prompt(
            goal,
            tools=tools,
            context=context,
            extra=(
                f"<previous_plan>\n{previous}\n</previous_plan>\n\n"
                f"<why_it_failed>\n{failure_reason}\n</why_it_failed>\n\n"
                f"<observations>\n{observed}\n</observations>"
            ),
        )
        return await self._request(REPLANNER_SYSTEM, prompt)

    # ------------------------------------------------------------- internals #
    def _build_prompt(
        self,
        goal: str,
        *,
        tools: list[Tool],
        context: str,
        extra: str = "",
    ) -> str:
        tool_lines = (
            "\n".join(
                f"- {tool.name} ({tool.permission.value}): {tool.description}"
                for tool in tools
            )
            or "- (no tools available; plan a direct answer)"
        )
        parts = [f"<goal>\n{goal}\n</goal>", f"<available_tools>\n{tool_lines}\n</available_tools>"]
        if context:
            parts.append(context)
        if extra:
            parts.append(extra)
        return "\n\n".join(parts)

    async def _request(self, system: str, prompt: str) -> Plan:
        response = await self._llm.complete(
            [LLMMessage.user(prompt)],
            system=system,
            response_schema=PLAN_SCHEMA,
            max_tokens=4_000,
        )
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise LLMResponseFormatError(
                "The planner did not return valid JSON.",
                details={"stop_reason": response.stop_reason},
            ) from exc

        return self._to_plan(payload)

    def _to_plan(self, payload: dict[str, Any]) -> Plan:
        raw_steps = payload.get("steps") or []
        if not isinstance(raw_steps, list) or not raw_steps:
            raise LLMResponseFormatError("The planner returned an empty plan.")

        steps = [
            PlanStep(
                index=index,
                description=str(step.get("description", "")).strip() or "(unspecified)",
                tool=step.get("tool") or None,
                expected_outcome=step.get("expected_outcome"),
            )
            for index, step in enumerate(raw_steps[: self._max_steps])
        ]
        return Plan(
            goal_restatement=str(payload.get("goal_restatement", "")),
            steps=steps,
            requires_tools=bool(payload.get("requires_tools", False)),
            reasoning=payload.get("reasoning"),
        )
