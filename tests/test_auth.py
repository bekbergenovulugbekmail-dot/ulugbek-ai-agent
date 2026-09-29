"""Operator authentication.

The properties here are the ones whose absence is invisible: an endpoint that
forgot the guard looks identical to one that has it until someone tries, and a
token that reaches a log looks like nothing at all.
"""

from __future__ import annotations

import logging
import re
import time

import pytest
from httpx import ASGITransport, AsyncClient

from tests.conftest import OPERATOR_TOKEN
from ulugbek_ai.api.auth import (
    configured_token_problem,
    issue_stream_token,
    stream_token_problem,
    token_matches,
)
from ulugbek_ai.config.settings import Settings
from ulugbek_ai.main import create_app

AUTH = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}

#: `{run_id}` and friends — every path parameter in this API is a UUID.
_PARAM = re.compile(r"\{[^}]+\}")

#: Endpoints that must never answer without a credential. One per router, plus
#: everything that was open before the guard moved to the router.
PROTECTED = [
    ("GET", "/api/agent/runs"),
    ("POST", "/api/agent/run"),
    ("POST", "/api/agent/runs"),
    ("GET", "/api/projects"),
    ("POST", "/api/projects"),
    ("GET", "/api/tasks"),
    ("GET", "/api/memory"),
    ("GET", "/api/memory/search"),
    ("GET", "/api/approvals"),
    ("GET", "/api/events"),
    ("GET", "/api/events/state"),
    ("GET", "/api/tools"),
    ("GET", "/api/tools/executions"),
    ("GET", "/api/system/overview"),
    ("POST", "/api/events/stream-token"),
]


# --------------------------------------------------------------------------- #
# No credential, wrong credential
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(("method", "path"), PROTECTED)
async def test_an_unauthenticated_request_is_refused(
    anonymous_client: AsyncClient, method: str, path: str
) -> None:
    response = await anonymous_client.request(method, path, json={"message": "x"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"
    # Without the challenge header a 401 is not something a client can act on.
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize(
    "header",
    [
        "Bearer wrong-token-entirely",
        f"Bearer {OPERATOR_TOKEN}x",
        f"Bearer {OPERATOR_TOKEN[:-1]}",
        f"Basic {OPERATOR_TOKEN}",
        "Bearer ",
        OPERATOR_TOKEN,
    ],
)
async def test_a_bad_credential_is_refused(
    anonymous_client: AsyncClient, header: str
) -> None:
    response = await anonymous_client.get(
        "/api/projects", headers={"Authorization": header}
    )

    assert response.status_code == 401


async def test_the_refusal_does_not_say_which_half_was_wrong(
    anonymous_client: AsyncClient,
) -> None:
    """Telling a caller their token was close is telling them to keep going."""
    missing = await anonymous_client.get("/api/projects")
    wrong = await anonymous_client.get(
        "/api/projects", headers={"Authorization": "Bearer nearly-right"}
    )

    assert missing.json()["error"] == wrong.json()["error"]


async def test_an_authenticated_request_is_served(client: AsyncClient) -> None:
    response = await client.get("/api/projects")

    assert response.status_code == 200


# --------------------------------------------------------------------------- #
# The Anthropic key is not a user credential
# --------------------------------------------------------------------------- #
async def test_the_anthropic_key_does_not_authenticate_a_user(
    database, settings: Settings, llm, registry
) -> None:
    """The obvious shortcut: reach for the key already in the environment.

    A credential that pays for model calls must not also open the front door.
    """
    from ulugbek_ai.api.deps import database_dependency, session_dependency

    configured = settings.model_copy(
        update={"anthropic_api_key": settings.anthropic_api_key}
    )
    app = create_app(configured)
    app.dependency_overrides[database_dependency] = lambda: database

    async def override_session():
        async with database.session() as session:
            yield session

    app.dependency_overrides[session_dependency] = override_session
    app.state.llm = llm
    app.state.registry = registry

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        for candidate in ("sk-ant-api03-whatever", "not-the-operator-token"):
            response = await http.get(
                "/api/projects", headers={"Authorization": f"Bearer {candidate}"}
            )
            assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Health stays reachable, and stays quiet
# --------------------------------------------------------------------------- #
async def test_health_answers_without_a_credential(
    anonymous_client: AsyncClient,
) -> None:
    """A platform has to ask whether the service is alive."""
    response = await anonymous_client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_health_reports_configuration_without_revealing_it(
    anonymous_client: AsyncClient,
) -> None:
    body = (await anonymous_client.get("/api/health")).text

    assert OPERATOR_TOKEN not in body
    for field in ("auth_token", "AUTH_TOKEN", "anthropic_api_key"):
        assert field not in body


async def test_health_says_whether_anyone_can_authenticate_at_all(
    anonymous_client: AsyncClient,
) -> None:
    """A server with no usable token refuses everything; that must be visible.

    Two booleans and nothing else — the value, its length and any complaint
    about it stay on the 503 the protected routes already answer with.
    """
    auth = (await anonymous_client.get("/api/health")).json()["auth"]

    assert auth == {"configured": True, "usable": True}


# --------------------------------------------------------------------------- #
# The token does not reach a log
# --------------------------------------------------------------------------- #
async def test_a_refused_token_is_not_written_to_the_log(
    anonymous_client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    """A log is the likeliest place for a credential to end up by accident."""
    secret_looking = "operator-token-that-must-not-appear-in-any-log"

    with caplog.at_level(logging.DEBUG):
        await anonymous_client.get(
            "/api/projects", headers={"Authorization": f"Bearer {secret_looking}"}
        )

    assert secret_looking not in caplog.text


async def test_an_accepted_token_is_not_written_to_the_log(
    client: AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        await client.get("/api/projects")

    assert OPERATOR_TOKEN not in caplog.text


# --------------------------------------------------------------------------- #
# The server decides who the caller is
# --------------------------------------------------------------------------- #
async def test_a_caller_cannot_name_itself_in_an_approval(
    client: AsyncClient,
) -> None:
    """`decided_by` is an audit fact, so the body has no say in it."""
    from ulugbek_ai.approvals.schemas import ApprovalDecision

    assert "decided_by" not in ApprovalDecision.model_fields


async def test_a_caller_cannot_claim_a_user_id(client: AsyncClient) -> None:
    """The route drops whatever the body said about ownership."""
    from ulugbek_ai.agent.schemas import AgentRunRequest
    from ulugbek_ai.api.auth import OPERATOR
    from ulugbek_ai.api.routes.agent import _owned_by

    claimed = AgentRunRequest(message="x", user_id=uuid_of("11111111" * 4))

    assert _owned_by(claimed, OPERATOR).user_id is None


def uuid_of(hex_text: str):
    import uuid

    return uuid.UUID(hex_text[:32])


# --------------------------------------------------------------------------- #
# Stream tokens
# --------------------------------------------------------------------------- #
async def test_the_stream_refuses_a_request_with_no_token(
    anonymous_client: AsyncClient,
) -> None:
    response = await anonymous_client.get(
        "/api/events/runs/11111111-1111-1111-1111-111111111111/stream"
    )

    assert response.status_code == 401


async def test_the_stream_accepts_a_minted_token(client: AsyncClient) -> None:
    minted = (await client.post("/api/events/stream-token")).json()

    assert minted["expires_in"] > 0
    problem = stream_token_problem(OPERATOR_TOKEN, minted["token"])
    assert problem is None


async def test_an_expired_stream_token_is_refused(
    anonymous_client: AsyncClient,
) -> None:
    expired = issue_stream_token(
        OPERATOR_TOKEN, ttl_seconds=60, now=time.time() - 600
    )

    response = await anonymous_client.get(
        "/api/events/runs/11111111-1111-1111-1111-111111111111/stream",
        params={"token": expired},
    )

    assert response.status_code == 401
    assert "expired" in response.json()["error"]["message"]


async def test_a_forged_stream_token_is_refused(
    anonymous_client: AsyncClient,
) -> None:
    forged = issue_stream_token("a-different-secret-entirely-xxxxx", ttl_seconds=60)

    response = await anonymous_client.get(
        "/api/events/runs/11111111-1111-1111-1111-111111111111/stream",
        params={"token": forged},
    )

    assert response.status_code == 401


def test_a_stream_token_opens_nothing_else() -> None:
    """It is scoped by what it is signed over, not by convention."""
    token = issue_stream_token(OPERATOR_TOKEN, ttl_seconds=60)

    assert not token_matches(token, OPERATOR_TOKEN)


# --------------------------------------------------------------------------- #
# A token too short to be worth having
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [None, "", "short", "x" * 31])
def test_a_weak_operator_token_is_rejected(value: str | None) -> None:
    assert configured_token_problem(value) is not None


def test_a_long_operator_token_is_accepted() -> None:
    assert configured_token_problem("x" * 32) is None


async def test_an_unconfigured_server_says_so_rather_than_refusing_callers(
    database, llm, registry
) -> None:
    """503, not 401: nothing the caller sends could possibly work."""
    from ulugbek_ai.api.deps import database_dependency, session_dependency

    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        auth_token=None,
    )
    app = create_app(settings)
    app.dependency_overrides[database_dependency] = lambda: database

    async def override_session():
        async with database.session() as session:
            yield session

    app.dependency_overrides[session_dependency] = override_session
    app.state.llm = llm
    app.state.registry = registry

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.get("/api/projects", headers=AUTH)
        assert response.status_code == 503
        assert "AUTH_TOKEN" in response.json()["error"]["message"]

        # And it still answers the question a platform asks.
        assert (await http.get("/api/health")).status_code == 200


# --------------------------------------------------------------------------- #
# Nothing slips through later
# --------------------------------------------------------------------------- #
#: Reachable without the operator token, each for a written reason. These are
#: the paths as the routes themselves declare them — the ``/api`` prefix is
#: added by the including router and is not part of a nested route's path.
OPEN_BY_DESIGN = {
    "/health",
    "/health/tools",
    # Proves itself with a signed stream token: EventSource sends no headers.
    "/events/runs/{run_id}/stream",
    # The app's own banner and the generated documentation.
    "/",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/openapi.json",
}


def _api_routes(app) -> list:
    """Every route the app serves, including the ones inside included routers.

    This FastAPI version wraps an included router rather than flattening it
    into ``app.routes``, so a walk of the top level finds exactly one route and
    any check built on it proves nothing. The negative control below is what
    caught that, and is why it exists.
    """
    from fastapi.routing import APIRoute

    found: list[APIRoute] = []
    seen: set[int] = set()

    def walk(routes) -> None:
        for route in routes:
            if id(route) in seen:
                continue
            seen.add(id(route))
            if isinstance(route, APIRoute):
                found.append(route)
            nested = getattr(route, "routes", None)
            if nested:
                walk(nested)
            included = getattr(route, "original_router", None)
            if included is not None:
                walk(included.routes)

    walk(app.routes)
    return found


async def test_every_route_refuses_an_anonymous_caller(
    anonymous_client: AsyncClient, settings: Settings
) -> None:
    """Every route, called for real without a credential.

    Checking how the guard is *wired* turned out to prove nothing: this
    FastAPI version attaches a router-level dependency to the wrapper rather
    than to the nested route, so an inspection of the route objects reported
    nineteen endpoints as unguarded while all nineteen were in fact returning
    401. Calling them is the only check that cannot be fooled by the wiring,
    and it is what a client experiences anyway.

    Twenty-one endpoints once had no guard at all. This fails the moment one
    is added outside the protected routers without a line in OPEN_BY_DESIGN.
    """
    app = create_app(settings)
    routes = _api_routes(app)
    assert routes, "no routes found — the walker is broken"

    checked = 0
    for route in routes:
        if route.path in OPEN_BY_DESIGN:
            continue
        path = _PARAM.sub("11111111-1111-1111-1111-111111111111", route.path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            response = await anonymous_client.request(
                method, f"/api{path}", json={"message": "x"}
            )
            assert response.status_code == 401, (
                f"{method} /api{path} answered {response.status_code} "
                "without a credential"
            )
            checked += 1

    # Guards against the walk silently finding nothing to check.
    assert checked >= 25, f"only {checked} routes exercised"


async def test_the_anonymous_check_would_notice_an_open_route(
    anonymous_client: AsyncClient, settings: Settings
) -> None:
    """A test that cannot fail is worse than none."""
    app = create_app(settings)
    open_paths = [
        route.path for route in _api_routes(app) if route.path in OPEN_BY_DESIGN
    ]

    assert "/health" in open_paths
    # The one route deliberately reachable without the operator token answers,
    # which is what makes the assertion above meaningful for the rest.
    assert (await anonymous_client.get("/api/health")).status_code == 200
