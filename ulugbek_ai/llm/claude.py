"""Anthropic Claude adapter.

This is the *only* module in the codebase that imports the Anthropic SDK.
Everything above it depends on :class:`~ulugbek_ai.llm.base.LLMClient`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

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

#: Names the workspace a multi-workspace key should act in.
WORKSPACE_HEADER = "anthropic-workspace-id"

#: Anthropic workspace ids are opaque, but their shape is documented and they
#: travel in a header, so anything with a space, a quote or a control character
#: in it cannot be one.
_WORKSPACE_ID_RE: Final[re.Pattern[str]] = re.compile(r"^wrkspc_[A-Za-z0-9_-]+$")


def workspace_id_problem(value: str | None) -> str | None:
    """Why *value* cannot be a workspace id, or ``None`` if it could be.

    Checked before the first request rather than after it: sent as-is, a
    malformed id comes back as an opaque 400 from Anthropic in the middle of a
    run, naming a header the operator never set by hand. The value itself is
    never included in the result — it goes into logs and API responses.
    """
    if value is None:
        return None
    if not value:
        return "it is empty"
    if any(character.isspace() for character in value):
        return "it contains whitespace"
    if any(character in value for character in ("\"", "'")):
        return "it is wrapped in quotes"
    if not value.isascii() or not value.isprintable():
        return "it contains characters that cannot travel in a header"
    if not _WORKSPACE_ID_RE.match(value):
        return (
            "it is not in the documented form: a workspace id begins with "
            "'wrkspc_'. An organization id or an account id will not work"
        )
    return None


# --------------------------------------------------------------------------- #
# Structured-output schemas
# --------------------------------------------------------------------------- #
#: Structured outputs accept only a subset of JSON Schema. These keywords are
#: rejected outright with a 400, so they are stripped before the request goes
#: out — the constraint is still enforced in our own code after the reply.
_UNSUPPORTED_SCHEMA_KEYWORDS: Final[tuple[str, ...]] = (
    "maxItems",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "pattern",
    "uniqueItems",
)

#: ``minItems`` is accepted, but only as 0 or 1.
_MAX_SUPPORTED_MIN_ITEMS: Final[int] = 1

#: How a dropped constraint is phrased for the model, so intent survives.
_CONSTRAINT_WORDING: Final[dict[str, str]] = {
    "maxItems": "at most {value} items",
    "minItems": "at least {value} items",
    "minimum": "at least {value}",
    "maximum": "at most {value}",
    "minLength": "at least {value} characters",
    "maxLength": "at most {value} characters",
    "pattern": "matching {value}",
}


def sanitize_structured_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a JSON Schema acceptable to ``output_config.format.schema``.

    Unsupported constraints are removed rather than sent and rejected, and each
    one is folded into the neighbouring ``description`` so the model still knows
    what was being asked for — the same trade the official SDKs make. The input
    is not modified.
    """
    return _sanitize_node(schema)


def _sanitize_node(node: Any) -> Any:
    if isinstance(node, list):
        return [_sanitize_node(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned: dict[str, Any] = {}
    dropped: list[str] = []

    for key, value in node.items():
        if key in _UNSUPPORTED_SCHEMA_KEYWORDS:
            dropped.append(_describe_constraint(key, value))
            continue
        if key == "minItems" and isinstance(value, int) and value > _MAX_SUPPORTED_MIN_ITEMS:
            dropped.append(_describe_constraint(key, value))
            cleaned[key] = _MAX_SUPPORTED_MIN_ITEMS
            continue
        cleaned[key] = _sanitize_node(value)

    if dropped:
        note = "Must be " + ", ".join(part for part in dropped if part) + "."
        existing = cleaned.get("description")
        cleaned["description"] = f"{existing} {note}".strip() if existing else note

    return cleaned


def _describe_constraint(keyword: str, value: Any) -> str:
    wording = _CONSTRAINT_WORDING.get(keyword)
    return wording.format(value=value) if wording else ""


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
        workspace_id: str | None = None,
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
        # A key that spans several workspaces runs in the workspace each request
        # names; without the header such a key is rejected outright. A header
        # that is present but malformed is rejected just as hard, so it is
        # checked here — before any request — and the message names the
        # variable rather than the header.
        problem = workspace_id_problem(workspace_id)
        if problem is not None:
            raise ConfigurationError(
                f"ANTHROPIC_WORKSPACE_ID is not usable: {problem}. Copy it from "
                "the Anthropic Console under Settings > Workspaces; it looks "
                "like 'wrkspc_' followed by letters and digits. Leave the "
                "variable unset if the API key is scoped to a single workspace."
            )
        headers = {WORKSPACE_HEADER: workspace_id} if workspace_id else None
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
            default_headers=headers,
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
            workspace_id=settings.anthropic_workspace_id,
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
                "schema": sanitize_structured_output_schema(response_schema),
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
            message = redact_text(str(exc.message))
            if "must be a valid workspace" in message.lower():
                # Anthropic names the header; the operator set a variable.
                message = (
                    "ANTHROPIC_WORKSPACE_ID is set but Anthropic does not "
                    "recognise it. Copy it from the Console under Settings > "
                    "Workspaces — it looks like 'wrkspc_' followed by letters "
                    "and digits, and is not the organization or account id."
                )
            elif "not scoped to a workspace" in message:
                # Point at the fix rather than echoing the API's phrasing.
                message = (
                    "This API key spans several workspaces, so each request "
                    "must name one. Set ANTHROPIC_WORKSPACE_ID (find it in the "
                    "Console under Settings > Workspaces), or use an API key "
                    "scoped to a single workspace."
                )
            raise LLMError(
                f"Claude API error (HTTP {exc.status_code}): {message}",
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
