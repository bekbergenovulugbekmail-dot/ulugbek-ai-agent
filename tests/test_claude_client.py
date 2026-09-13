"""Claude adapter: request shaping, response normalization, error translation.

The Anthropic SDK is replaced by a stub, so these tests assert the contract we
depend on without ever making a network call.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.errors import ConfigurationError, LLMError, LLMTimeoutError
from ulugbek_ai.llm.base import LLMMessage, LLMToolSpec
from ulugbek_ai.llm.claude import ClaudeClient, sanitize_structured_output_schema


class _Messages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.last_request: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.last_request = kwargs
        if self._error is not None:
            raise self._error
        return self._response


class _StubSDK:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.messages = _Messages(response, error)

    async def close(self) -> None:
        return None


def _block(**kwargs: Any) -> SimpleNamespace:
    payload = dict(kwargs)
    return SimpleNamespace(to_dict=lambda: payload, **kwargs)


def _message(
    content: list[Any], *, stop_reason: str = "end_turn", **extra: Any
) -> SimpleNamespace:
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        model="claude-opus-5",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        **extra,
    )


def _client(sdk: _StubSDK, **kwargs: Any) -> ClaudeClient:
    return ClaudeClient(api_key="sk-ant-test-key", client=sdk, **kwargs)


def test_a_missing_key_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY"):
        ClaudeClient(api_key="")


def test_settings_are_carried_into_the_client() -> None:
    settings = Settings(
        _env_file=None,
        anthropic_api_key="sk-ant-test-key",
        claude_model="claude-opus-5",
    )
    assert ClaudeClient.from_settings(settings).model == "claude-opus-5"


async def test_request_shape() -> None:
    sdk = _StubSDK(_message([_block(type="text", text="hello")]))
    client = _client(sdk, model="claude-opus-5", effort="high", thinking=True)

    await client.complete(
        [LLMMessage.user("hi")],
        system="be brief",
        tools=[
            LLMToolSpec("echo", "echo it", {"type": "object", "properties": {}})
        ],
    )
    request = sdk.messages.last_request

    assert request["model"] == "claude-opus-5"
    assert request["system"] == "be brief"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"]["effort"] == "high"
    assert request["tools"][0]["name"] == "echo"
    assert request["messages"][0]["role"] == "user"


async def test_a_json_schema_becomes_output_config_format() -> None:
    sdk = _StubSDK(_message([_block(type="text", text='{"a": 1}')]))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}

    response = await _client(sdk).complete(
        [LLMMessage.user("hi")], response_schema=schema
    )

    assert sdk.messages.last_request["output_config"]["format"] == {
        "type": "json_schema",
        "schema": schema,
    }
    assert response.json() == {"a": 1}


async def test_thinking_is_not_disabled_at_high_effort_levels() -> None:
    """``thinking: disabled`` is rejected at xhigh/max, so it must be omitted."""
    sdk = _StubSDK(_message([_block(type="text", text="ok")]))

    await _client(sdk, thinking=False, effort="max").complete([LLMMessage.user("hi")])
    assert "thinking" not in sdk.messages.last_request

    sdk2 = _StubSDK(_message([_block(type="text", text="ok")]))
    await _client(sdk2, thinking=False, effort="medium").complete(
        [LLMMessage.user("hi")]
    )
    assert sdk2.messages.last_request["thinking"] == {"type": "disabled"}


async def test_tool_calls_are_normalized() -> None:
    sdk = _StubSDK(
        _message(
            [
                _block(type="text", text="let me check"),
                _block(
                    type="tool_use",
                    id="toolu_1",
                    name="current_time",
                    input={"timezone": "Asia/Tashkent"},
                ),
            ],
            stop_reason="tool_use",
        )
    )

    response = await _client(sdk).complete([LLMMessage.user("what time is it?")])

    assert response.has_tool_calls
    assert response.tool_calls[0].name == "current_time"
    assert response.tool_calls[0].arguments == {"timezone": "Asia/Tashkent"}
    assert response.text == "let me check"
    assert response.usage.input_tokens == 10


async def test_raw_blocks_are_preserved_for_replay() -> None:
    """Thinking blocks must survive a round trip through the transcript."""
    sdk = _StubSDK(
        _message(
            [
                _block(type="thinking", thinking="", signature="sig"),
                _block(type="text", text="done"),
            ]
        )
    )

    response = await _client(sdk).complete([LLMMessage.user("hi")])
    replayed = response.as_message().to_dict()

    assert replayed["role"] == "assistant"
    assert replayed["content"][0]["type"] == "thinking"
    assert replayed["content"][0]["signature"] == "sig"


async def test_a_refusal_is_surfaced_as_an_error() -> None:
    sdk = _StubSDK(
        _message(
            [],
            stop_reason="refusal",
            stop_details=SimpleNamespace(category="cyber", explanation="no"),
        )
    )

    with pytest.raises(LLMError) as exc_info:
        await _client(sdk).complete([LLMMessage.user("hi")])

    assert exc_info.value.details["category"] == "cyber"


async def test_a_timeout_is_translated() -> None:
    sdk = _StubSDK(error=anthropic.APITimeoutError(request=None))
    with pytest.raises(LLMTimeoutError):
        await _client(sdk).complete([LLMMessage.user("hi")])


async def test_a_connection_error_is_translated() -> None:
    sdk = _StubSDK(error=anthropic.APIConnectionError(request=None))
    with pytest.raises(LLMError, match="Could not reach"):
        await _client(sdk).complete([LLMMessage.user("hi")])


def test_transcript_round_trips_through_json() -> None:
    message = LLMMessage.user("hello")
    assert LLMMessage.from_dict(message.to_dict()).text == "hello"
    assert LLMMessage.from_dict({"role": "user", "content": "plain"}).text == "plain"


# --------------------------------------------------------------------------- #
# Workspace scoping
# --------------------------------------------------------------------------- #
def test_a_workspace_id_is_sent_as_a_header() -> None:
    """A key spanning several workspaces must name one on every request."""
    captured: dict[str, Any] = {}

    class _Recording(_StubSDK):
        pass

    real_ctor = anthropic.AsyncAnthropic

    def fake_ctor(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _Recording(_message([_block(type="text", text="ok")]))

    anthropic.AsyncAnthropic = fake_ctor  # type: ignore[assignment]
    try:
        ClaudeClient(api_key="sk-ant-test-key", workspace_id="wrkspc_01ABC")
    finally:
        anthropic.AsyncAnthropic = real_ctor  # type: ignore[assignment]

    assert captured["default_headers"] == {
        "anthropic-workspace-id": "wrkspc_01ABC"
    }


def test_no_workspace_header_when_none_is_configured() -> None:
    captured: dict[str, Any] = {}
    real_ctor = anthropic.AsyncAnthropic

    def fake_ctor(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _StubSDK(_message([]))

    anthropic.AsyncAnthropic = fake_ctor  # type: ignore[assignment]
    try:
        ClaudeClient(api_key="sk-ant-test-key")
    finally:
        anthropic.AsyncAnthropic = real_ctor  # type: ignore[assignment]

    assert captured["default_headers"] is None


def test_the_workspace_id_comes_from_settings() -> None:
    settings = Settings(
        _env_file=None,
        anthropic_api_key="sk-ant-test-key",
        anthropic_workspace_id="wrkspc_01XYZ",
    )
    captured: dict[str, Any] = {}
    real_ctor = anthropic.AsyncAnthropic

    def fake_ctor(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _StubSDK(_message([]))

    anthropic.AsyncAnthropic = fake_ctor  # type: ignore[assignment]
    try:
        ClaudeClient.from_settings(settings)
    finally:
        anthropic.AsyncAnthropic = real_ctor  # type: ignore[assignment]

    assert captured["default_headers"]["anthropic-workspace-id"] == "wrkspc_01XYZ"


async def test_the_workspace_error_explains_the_fix() -> None:
    """The API's wording does not say what to do; ours must."""
    sdk = _StubSDK(
        error=anthropic.APIStatusError(
            "This API key is not scoped to a workspace, so this request must "
            "include the workspace with the ID of the workspace to use.",
            response=httpx.Response(400, request=httpx.Request("POST", "https://x")),
            body=None,
        )
    )

    with pytest.raises(LLMError) as exc_info:
        await _client(sdk).complete([LLMMessage.user("hi")])

    assert "ANTHROPIC_WORKSPACE_ID" in exc_info.value.message
    assert "scoped to a single workspace" in exc_info.value.message


# --------------------------------------------------------------------------- #
# Structured-output schema compatibility
# --------------------------------------------------------------------------- #
def test_unsupported_schema_keywords_are_stripped() -> None:
    """Structured outputs reject these with a 400, so they never go out."""
    cleaned = sanitize_structured_output_schema(
        {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 10,
                    "items": {"type": "string", "maxLength": 50, "pattern": "^a"},
                },
                "score": {"type": "integer", "minimum": 0, "maximum": 5},
            },
            "required": ["steps"],
            "additionalProperties": False,
        }
    )
    rendered = json.dumps(cleaned)

    for keyword in ("maxItems", "maxLength", "pattern", "minimum", "maximum"):
        assert keyword not in rendered
    # Supported keywords survive untouched.
    assert cleaned["properties"]["steps"]["minItems"] == 1
    assert cleaned["required"] == ["steps"]
    assert cleaned["additionalProperties"] is False


def test_a_dropped_constraint_is_explained_to_the_model() -> None:
    """Removing a rule silently would change what we are asking for."""
    cleaned = sanitize_structured_output_schema(
        {
            "type": "array",
            "maxItems": 10,
            "description": "The plan steps.",
            "items": {"type": "string"},
        }
    )

    assert "The plan steps." in cleaned["description"]
    assert "at most 10 items" in cleaned["description"]


def test_min_items_is_clamped_to_what_the_api_accepts() -> None:
    """Only 0 and 1 are supported values."""
    cleaned = sanitize_structured_output_schema(
        {"type": "array", "minItems": 3, "items": {"type": "string"}}
    )

    assert cleaned["minItems"] == 1
    assert "at least 3 items" in cleaned["description"]


def test_sanitizing_does_not_modify_the_original_schema() -> None:
    original = {"type": "array", "maxItems": 4, "items": {"type": "string"}}
    sanitize_structured_output_schema(original)

    assert original["maxItems"] == 4


def test_the_planner_and_verifier_schemas_are_accepted_as_sent() -> None:
    """Both schemas this app actually sends must survive the API's subset."""
    from ulugbek_ai.agent.planner import PLAN_SCHEMA
    from ulugbek_ai.agent.verifier import VERIFICATION_SCHEMA

    for schema in (PLAN_SCHEMA, VERIFICATION_SCHEMA):
        rendered = json.dumps(sanitize_structured_output_schema(schema))
        for keyword in (
            "maxItems",
            "maxLength",
            "minLength",
            "pattern",
            "minimum",
            "maximum",
        ):
            assert keyword not in rendered, f"{keyword} still present"


async def test_the_request_carries_the_sanitized_schema() -> None:
    sdk = _StubSDK(_message([_block(type="text", text="{}")]))

    await _client(sdk).complete(
        [LLMMessage.user("hi")],
        response_schema={
            "type": "object",
            "properties": {"a": {"type": "array", "maxItems": 3}},
        },
    )

    sent = json.dumps(sdk.messages.last_request["output_config"]["format"]["schema"])
    assert "maxItems" not in sent
