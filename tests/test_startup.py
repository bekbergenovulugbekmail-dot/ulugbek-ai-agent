"""Starting up with a half-finished configuration.

A hosting platform is where a credential is most likely to be missing, and it
is the one place nobody can attach a debugger. Everything here is about what
the service does when something it needs is not there yet: it must come up far
enough to say so.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest
from starlette.testclient import TestClient

from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.errors import ConfigurationError
from ulugbek_ai.llm.claude import (
    WORKSPACE_HEADER,
    ClaudeClient,
    workspace_id_problem,
)
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


# --------------------------------------------------------------------------- #
# The container entrypoint
# --------------------------------------------------------------------------- #
def run_entrypoint(database_url: str, **env: str) -> subprocess.CompletedProcess[str]:
    root = pathlib.Path(__file__).resolve().parent.parent
    return subprocess.run(
        ["sh", "entrypoint.sh"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **os.environ,
            "DATABASE_URL": database_url,
            "MIGRATION_ATTEMPTS": "1",
            "MIGRATION_RETRY_SECONDS": "1",
            **env,
        },
    )


def test_an_unreachable_database_fails_with_something_to_act_on() -> None:
    """The failure an operator actually meets, and the one they cannot debug.

    A single failed migration attempt used to exit the container before the
    server started, so /api/health — built to report `database.connected:
    false` — never answered and the platform served an opaque 502.
    """
    result = run_entrypoint("postgresql+asyncpg://nobody:nothing@127.0.0.1:59999/none")

    assert result.returncode == 1
    assert "database migrations failed" in result.stderr
    assert "DATABASE_URL" in result.stderr
    # It names the variable, never reads it out.
    assert "the database service is running and reachable" in result.stderr


def test_the_database_password_is_never_printed() -> None:
    """DATABASE_URL carries a password, and this runs in a platform log."""
    secret = "sup3rs3cret-not-in-any-log"
    result = run_entrypoint(
        f"postgresql+asyncpg://someone:{secret}@127.0.0.1:59999/none"
    )

    assert secret not in result.stderr
    assert secret not in result.stdout


def test_a_transient_database_failure_is_retried() -> None:
    """A database that is not ready *yet* is the ordinary case, not an error."""
    result = run_entrypoint(
        "postgresql+asyncpg://nobody:nothing@127.0.0.1:59999/none",
        MIGRATION_ATTEMPTS="3",
    )

    assert "attempt 1/3" in result.stdout
    assert "attempt 3/3" in result.stdout
    assert "retrying in" in result.stderr


# --------------------------------------------------------------------------- #
# The Anthropic workspace id
# --------------------------------------------------------------------------- #
WORKSPACE = "wrkspc_01ABCdef23456789"


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "\n", '""', "''", "'  '"],
)
def test_a_blank_or_quoted_workspace_id_reads_as_unset(raw: str) -> None:
    """A dashboard variable is as often blank or quote-wrapped as it is right."""
    assert settings_with(anthropic_workspace_id=raw).anthropic_workspace_id is None


@pytest.mark.parametrize(
    "raw",
    [f"  {WORKSPACE}\n", f'"{WORKSPACE}"', f"'{WORKSPACE}'", f'" {WORKSPACE} "'],
)
def test_a_pasted_workspace_id_is_cleaned_up(raw: str) -> None:
    """The value travels in a header, where a stray space is fatal and unseen."""
    assert settings_with(anthropic_workspace_id=raw).anthropic_workspace_id == WORKSPACE


@pytest.mark.parametrize("value", [None, WORKSPACE, "wrkspc_a-b_c"])
def test_a_usable_workspace_id_reports_no_problem(value: str | None) -> None:
    assert workspace_id_problem(value) is None


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        ("", "empty"),
        ("wrkspc_a b", "whitespace"),
        ('"wrkspc_a"', "quotes"),
        ("wrkspc_ünicode", "cannot travel in a header"),
        ("org_01ABCdef", "wrkspc_"),
        ("account-123", "wrkspc_"),
    ],
)
def test_a_malformed_workspace_id_says_what_is_wrong(
    value: str, fragment: str
) -> None:
    problem = workspace_id_problem(value)

    assert problem is not None
    assert fragment in problem


def test_a_malformed_workspace_id_is_caught_before_any_request() -> None:
    """The regression: it used to be caught by Anthropic, mid-run, as a 400.

    The message named a header the operator never set, on a request they did
    not know carried one.
    """
    with pytest.raises(ConfigurationError) as exc_info:
        ClaudeClient(api_key="sk-ant-test", workspace_id="org_01ABCdef")

    assert "ANTHROPIC_WORKSPACE_ID" in exc_info.value.message
    assert "wrkspc_" in exc_info.value.message


def test_the_workspace_id_is_never_in_the_message() -> None:
    """It is an identifier the operator treats as sensitive; keep it out."""
    secret_looking = "wrkspc_NOT-IN-ANY-MESSAGE"

    with pytest.raises(ConfigurationError) as exc_info:
        ClaudeClient(api_key="sk-ant-test", workspace_id=f"{secret_looking} ")

    assert secret_looking not in exc_info.value.message


def test_a_valid_workspace_id_is_sent_as_the_header() -> None:
    """The fix must not quietly drop a header the API key requires."""
    client = ClaudeClient(api_key="sk-ant-test", workspace_id=WORKSPACE)

    sent = client._client.default_headers  # noqa: SLF001 - the point of the test
    assert sent[WORKSPACE_HEADER] == WORKSPACE


def test_no_workspace_header_is_sent_when_none_is_configured() -> None:
    client = ClaudeClient(api_key="sk-ant-test", workspace_id=None)

    assert WORKSPACE_HEADER not in client._client.default_headers  # noqa: SLF001


def test_a_malformed_workspace_id_does_not_take_the_service_down() -> None:
    """Dying here would hide the explanation behind a platform 502."""
    settings = settings_with(
        anthropic_api_key="sk-ant-test", anthropic_workspace_id="org_01ABCdef"
    )

    assert build_llm_client(settings) is None

    app = create_app(settings)
    with TestClient(app) as client:
        body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert body["llm"]["workspace"] == {
        "configured": True,
        "usable": False,
        "problem": workspace_id_problem("org_01ABCdef"),
    }


def test_the_agent_endpoint_names_the_workspace_variable() -> None:
    """Not "set ANTHROPIC_API_KEY" — the key is fine, and that advice costs time."""
    settings = settings_with(
        anthropic_api_key="sk-ant-test", anthropic_workspace_id="org_01ABCdef"
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.post("/api/agent/run", json={"message": "hello"})

    assert response.status_code >= 400
    assert "ANTHROPIC_WORKSPACE_ID" in response.text
    assert "ANTHROPIC_API_KEY" not in response.text
