"""A scripted :class:`LLMClient` used by tests.

This is a **test double**, not a stand-in for the real client: it replays
responses a test supplies and records the requests it received, so the agent
loop can be exercised deterministically without network access. Production code
paths never construct it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ulugbek_ai.core.errors import LLMError
from ulugbek_ai.llm.base import (
    LLMClient,
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolSpec,
    LLMUsage,
)


@dataclass(slots=True)
class RecordedCall:
    """One captured request."""

    messages: list[LLMMessage]
    system: str | None
    tools: list[LLMToolSpec] | None
    response_schema: dict[str, Any] | None


class ScriptedLLMClient(LLMClient):
    """Replays a fixed list of responses in order."""

    def __init__(
        self,
        responses: list[LLMResponse] | None = None,
        *,
        model: str = "scripted-model",
    ) -> None:
        self.model = model
        self.responses: list[LLMResponse] = list(responses or [])
        self.calls: list[RecordedCall] = []

    def queue(self, response: LLMResponse) -> "ScriptedLLMClient":
        self.responses.append(response)
        return self

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        tools: list[LLMToolSpec] | None = None,
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
        effort: str | None = None,
        thinking: bool | None = None,
    ) -> LLMResponse:
        self.calls.append(
            RecordedCall(
                messages=list(messages),
                system=system,
                tools=list(tools) if tools else None,
                response_schema=response_schema,
            )
        )
        if not self.responses:
            raise LLMError("ScriptedLLMClient ran out of queued responses.")
        return self.responses.pop(0)


def text_response(text: str, *, stop_reason: str = "end_turn") -> LLMResponse:
    """Build a plain text reply."""
    return LLMResponse(
        text=text,
        content=[{"type": "text", "text": text}],
        stop_reason=stop_reason,
        usage=LLMUsage(input_tokens=1, output_tokens=1),
    )


def tool_response(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    call_id: str = "toolu_test_1",
    text: str = "",
) -> LLMResponse:
    """Build a reply that asks for one tool call."""
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    content.append(
        {"type": "tool_use", "id": call_id, "name": tool_name, "input": arguments}
    )
    return LLMResponse(
        text=text,
        tool_calls=[LLMToolCall(id=call_id, name=tool_name, arguments=arguments)],
        content=content,
        stop_reason="tool_use",
        usage=LLMUsage(input_tokens=1, output_tokens=1),
    )
