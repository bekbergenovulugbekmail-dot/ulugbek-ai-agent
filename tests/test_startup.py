"""Starting up with a half-finished configuration.

A hosting platform is where a credential is most likely to be missing, and it
is the one place nobody can attach a debugger. Everything here is about what
the service does when something it needs is not there yet: it must come up far
enough to say so.
"""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from ulugbek_ai.config.settings import Settings
from ulugbek_ai.main import build_llm_client, create_app


def settings_with(**overrides: object) -> Settings:
    overrides.setdefault("database_url", "sqlite+aiosqlite:///:memory:")
    return Settings(_env_file=None, environment="test", **overrides)


# --------------------------------------------------------------------------- #
# A blank variable is not a credential
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "field",
    ["anthropic_api_key", "github_token", "railway_token"],
)
@pytest.mark.parametrize("blank", ["", "   ", "\n", "\t "])
def test_a_blank_credential_reads_as_unset(field: str, blank: str) -> None:
    """`KEY=` in a dashboard or a deploy file means "not set yet".

    Read as a value instead, an empty key crashes startup and an empty token
    authenticates every request with an empty bearer.
    """
    assert getattr(settings_with(**{field: blank}), field) is None


@pytest.mark.parametrize(
    "field",
    ["anthropic_api_key", "github_token", "railway_token"],
)
def test_a_credential_pasted_with_whitespace_still_works(field: str) -> None:
    """A trailing newline is a paste artefact, not part of the key."""
    value = getattr(settings_with(**{field: "  sk-ant-example-value\n"}), field)

    assert value is not None
    assert value.get_secret_value() == "sk-ant-example-value"


def test_a_real_credential_is_left_alone() -> None:
    key = settings_with(anthropic_api_key="sk-ant-example-value").anthropic_api_key

    assert key is not None
    assert key.get_secret_value() == "sk-ant-example-value"


# --------------------------------------------------------------------------- #
# Starting without a model key
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", [None, "", "   "])
def test_no_model_client_is_built_without_a_key(key: str | None) -> None:
    overrides = {} if key is None else {"anthropic_api_key": key}

    assert build_llm_client(settings_with(**overrides)) is None


@pytest.mark.parametrize("key", [None, ""])
def test_the_service_starts_and_reports_itself_without_a_model_key(
    key: str | None,
) -> None:
    """The regression: a blank key used to abort startup.

    The container then restarts until the platform gives up, and the health
    endpoint — the one thing that would explain the problem — never answers.
    TestClient as a context manager runs the real lifespan, which is where the
    failure happened.
    """
    overrides = {} if key is None else {"anthropic_api_key": key}
    app = create_app(settings_with(**overrides))

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["llm"]["configured"] is False
    # The tools are registered regardless, so the agent can say what it would
    # be able to do once a key exists.
    assert body["tools"]["count"] > 0


def test_health_reports_a_configured_model_key() -> None:
    app = create_app(settings_with(anthropic_api_key="sk-ant-example-value"))

    with TestClient(app) as client:
        body = client.get("/api/health").json()

    assert body["llm"]["configured"] is True
    # Whether a key exists, never the key.
    assert "sk-ant-example-value" not in json.dumps(body)


# --------------------------------------------------------------------------- #
# The database URL a platform actually injects
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "injected",
    [
        "postgres://user:pw@host:5432/db",
        "postgresql://user:pw@host:5432/db",
    ],
)
def test_a_platform_database_url_is_upgraded_to_the_async_driver(
    injected: str,
) -> None:
    """Railway and most managed providers inject the sync form."""
    assert settings_with(database_url=injected).database_url.startswith(
        "postgresql+asyncpg://"
    )
