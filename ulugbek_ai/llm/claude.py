"""Anthropic Claude adapter.

This is the *only* module in the codebase that imports the Anthropic SDK.
Everything above it depends on :class:`~ulugbek_ai.llm.base.LLMClient`.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.errors import ConfigurationError, LLMError, LLMTimeoutError
from ulugbek_ai.core.redaction import redact_text
from ulugbek_ai.llm.base import (
    ContentBlock,
    LLMClient,
    LLMMessage,
    LLMResponse,
    LLMToolCall,
    LLMToolSpec,
    LLMUsage,
)

logger = logging.getLogger(__name__)

#: Effort levels that are incompatible with thinking being switched off.
_HIGH_EFFORT_LEVELS = frozenset({"xhigh", "max"})


class ClaudeClient(LLMClient):
    """Async Claude client built on :class:`anthropic.AsyncAnthropic`."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-opus-5",
        max_tokens: int = 16_000,
        effort: str = "high",
        thinking: bool = True,
        timeout_seconds: float = 120.0,
        max_retries: int = 2,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        if not api_key:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY is not set. Export it in the environment "
                "or add it to .env (never commit the value)."
            )
        self.model = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._thinking = thinking
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    # ------------------------------------------------------------------ ctor #
    @classmethod
    def from_settings(cls, settings: Settings) -> "ClaudeClient":
        """Build a client from application settings."""
        key = settings.anthropic_api_key
        return cls(
            api_key=key.get_secret_value() if key else "",
            model=settings.claude_model,
            max_tokens=settings.claude_max_tokens,
            effort=settings.claude_effort,
            thinking=settings.claude_thinking,
            timeout_seconds=settings.claude_timeout_seconds,
            max_retries=settings.claude_max_retries,
        )

    # --------------------------------------------------------------- request #
    def _build_request(
        self,
        messages: list[LLMMessage],
        system: str | None,
        tools: list[LLMToolSpec] | None,
        max_tokens: int | None,
        response_schema: dict[str, Any] | None,
        effort: str | None,
        thinking: bool | None,
    ) -> dict[str, Any]:
        effort_level = effort or self._effort
        thinking_on = self._thinking if thinking is None else thinking

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self._max_tokens,
            "messages": [message.to_dict() for message in messages],
        }

        output_config: dict[str, Any] = {"effort": effort_level}

        if system:
            payload["system"] = system

        if thinking_on:
            payload["thinking"] = {"type": "adaptive"}
        elif effort_level not in _HIGH_EFFORT_LEVELS:
            # Disabling thinking is rejected at xhigh/max effort, so only send
            # the opt-out where the API accepts it.
            payload["thinking"] = {"type": "disabled"}

        if tools:
            payload["tools"] = [tool.to_wire() for tool in tools]

        if response_schema is not None:
            output_config["format"] = {
                "type": "json_schema",
                "schema": response_schema,
            }

        payload["output_config"] = output_config
        return payload

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
        request = self._build_request(
            messages, system, tools, max_tokens, response_schema, effort, thinking
        )

        try:
            response = await self._client.messages.create(**request)
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError(
                f"Claude request timed out: {redact_text(str(exc))}"
            ) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(
                f"Claude API error (HTTP {exc.status_code}): "
                f"{redact_text(str(exc.message))}",
                details={"status_code": exc.status_code},
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(
                f"Could not reach the Claude API: {redact_text(str(exc))}"
            ) from exc

        return self._parse_response(response)

    # -------------------------------------------------------------- response #
    @staticmethod
    def _parse_response(response: Any) -> LLMResponse:
        """Normalize an Anthropic ``Message`` into an :class:`LLMResponse`."""
        blocks: list[ContentBlock] = []
        texts: list[str] = []
        tool_calls: list[LLMToolCall] = []

        for block in response.content:
            blocks.append(_block_to_dict(block))
            block_type = getattr(block, "type", None)
            if block_type == "text":
                texts.append(block.text)
            elif block_type == "tool_use":
                tool_calls.append(
                    LLMToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input or {}),
                    )
                )

        usage = LLMUsage()
        raw_usage = getattr(response, "usage", None)
        if raw_usage is not None:
            usage = LLMUsage(
                input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
                output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
                cache_read_input_tokens=(
                    getattr(raw_usage, "cache_read_input_tokens", 0) or 0
                ),
                cache_creation_input_tokens=(
                    getattr(raw_usage, "cache_creation_input_tokens", 0) or 0
                ),
            )

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            raise LLMError(
                "Claude declined the request.",
                details={"stop_reason": stop_reason, "category": category},
            )

        return LLMResponse(
            text="\n".join(texts).strip(),
            tool_calls=tool_calls,
            content=blocks,
            stop_reason=stop_reason,
            usage=usage,
            model=getattr(response, "model", None),
        )

    async def aclose(self) -> None:
        await self._client.close()


def _block_to_dict(block: Any) -> ContentBlock:
    """Serialize an SDK content block to a plain JSON-safe dict.

    The result is stored in the database and replayed to the API verbatim, so
    thinking blocks and tool_use blocks survive across process restarts.
    """
    if isinstance(block, dict):
        return block
    to_dict = getattr(block, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    model_dump = getattr(block, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return {"type": getattr(block, "type", "unknown"), "value": str(block)}
