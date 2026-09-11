"""Provider-agnostic LLM interface.

The agent engine talks to :class:`LLMClient` only. Anthropic-specific request
and response shapes stay inside :mod:`ulugbek_ai.llm.claude`; swapping or adding
a provider means adding one class here, not touching the agent.

Transcripts are stored as JSON (``LLMMessage.content`` is a list of content
blocks), which keeps a paused run resumable after a process restart.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["user", "assistant"]

#: A single content block in the provider's own wire format.
ContentBlock = dict[str, Any]


@dataclass(slots=True)
class LLMToolSpec:
    """A tool as advertised to the model."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_wire(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass(slots=True)
class LLMToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class LLMUsage:
    """Token accounting for one call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=(
                self.cache_read_input_tokens + other.cache_read_input_tokens
            ),
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens + other.cache_creation_input_tokens
            ),
        )


@dataclass(slots=True)
class LLMMessage:
    """One turn of the transcript, serializable to and from JSON."""

    role: Role
    content: list[ContentBlock]

    @classmethod
    def user(cls, text: str) -> "LLMMessage":
        return cls(role="user", content=[{"type": "text", "text": text}])

    @classmethod
    def assistant(cls, content: list[ContentBlock]) -> "LLMMessage":
        return cls(role="assistant", content=content)

    @classmethod
    def tool_results(cls, results: list[ContentBlock]) -> "LLMMessage":
        """Tool results are sent back as a single ``user`` turn."""
        return cls(role="user", content=results)

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "LLMMessage":
        content = payload["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        return cls(role=payload["role"], content=list(content))

    @property
    def text(self) -> str:
        """Concatenated text of every text block in this message."""
        return "\n".join(
            block.get("text", "")
            for block in self.content
            if block.get("type") == "text"
        ).strip()


@dataclass(slots=True)
class LLMResponse:
    """A model reply, normalized across providers."""

    text: str
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    #: Raw provider content blocks — echoed back verbatim on the next turn so
    #: thinking blocks and tool_use blocks survive a multi-step loop.
    content: list[ContentBlock] = field(default_factory=list)
    stop_reason: str | None = None
    usage: LLMUsage = field(default_factory=LLMUsage)
    model: str | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def as_message(self) -> LLMMessage:
        """This reply as an assistant turn, ready to append to the transcript."""
        return LLMMessage.assistant(self.content)

    def json(self) -> Any:
        """Parse the reply text as JSON.

        Raises ``json.JSONDecodeError`` — callers that requested a JSON schema
        translate that into :class:`~ulugbek_ai.core.errors.LLMResponseFormatError`.
        """
        return json.loads(self.text)


@runtime_checkable
class SupportsClose(Protocol):
    async def aclose(self) -> None: ...


class LLMClient(ABC):
    """Interface every LLM provider adapter implements."""

    #: Identifier of the model this client talks to (for audit rows).
    model: str

    @abstractmethod
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
        """Run one completion.

        Args:
            messages: Full transcript, oldest first.
            system: System prompt.
            tools: Tools the model may call this turn.
            max_tokens: Output cap; falls back to the client default.
            response_schema: JSON Schema the reply must conform to. When set, the
                reply text is guaranteed-parseable JSON matching the schema.
            effort: Reasoning effort (``low`` … ``max``).
            thinking: Enable extended thinking for this call.

        Raises:
            LLMError: on any provider failure (already redacted).
        """

    async def aclose(self) -> None:
        """Release provider resources. Safe to call more than once."""
        return None
