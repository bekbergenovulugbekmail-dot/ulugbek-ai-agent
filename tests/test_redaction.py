"""Secrets must never reach a log, an audit row or an API response."""

from __future__ import annotations

import pytest

from ulugbek_ai.core.redaction import REDACTED, is_sensitive_key, redact, redact_text, truncate


@pytest.mark.parametrize(
    "text",
    [
        "key is sk-ant-api03-abcdefgh12345678ijkl",
        "Authorization: Bearer abcdef1234567890",
        "password=hunter2000",
        "api_key: 0123456789abcdef",
        "postgresql+asyncpg://user:hunter2@db:5432/app",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    ],
)
def test_known_secret_shapes_are_masked(text: str) -> None:
    redacted = redact_text(text)
    assert REDACTED in redacted
    for secret in ("hunter2000", "hunter2", "abcdefgh12345678ijkl", "abcdef1234567890"):
        assert secret not in redacted


def test_ordinary_text_is_left_alone() -> None:
    text = "Deploy the ERP project to staging at 14:00."
    assert redact_text(text) == text


def test_sensitive_keys_are_masked_whatever_the_value() -> None:
    payload = {
        "api_key": "not-obviously-a-secret",
        "nested": {"Token": "abc", "safe": "keep me"},
        "items": [{"password": "x"}, "sk-ant-0123456789abcdefgh"],
    }
    result = redact(payload)

    assert result["api_key"] == REDACTED
    assert result["nested"]["Token"] == REDACTED
    assert result["nested"]["safe"] == "keep me"
    assert result["items"][0]["password"] == REDACTED
    assert REDACTED in result["items"][1]


def test_redact_does_not_mutate_the_input() -> None:
    payload = {"api_key": "secret", "n": {"a": 1}}
    redact(payload)
    assert payload["api_key"] == "secret"


def test_deeply_nested_structures_terminate() -> None:
    payload: dict = {}
    cursor = payload
    for _ in range(50):
        cursor["next"] = {}
        cursor = cursor["next"]
    assert redact(payload) is not None


@pytest.mark.parametrize("key", ["API_KEY", "userPassword", "x-authorization"])
def test_key_detection_is_case_insensitive(key: str) -> None:
    assert is_sensitive_key(key)


def test_truncate_marks_the_cut() -> None:
    assert truncate("abcdef", 100) == "abcdef"
    result = truncate("x" * 100, 20)
    assert len(result) == 20
    assert result.endswith("[truncated]")
