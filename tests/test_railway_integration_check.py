"""The read-only Railway integration check.

The production backend holds a Railway token, and nothing outside it can see
whether that token works: `/health` carries no integrations block, and the
Railway tools register whether or not a token is configured, so their presence
in `/api/tools` proves nothing. The only way to find out is to make the
deployed agent use one and then read what happened.

The verdict is taken from the recorded tool executions rather than from what
the agent wrote in prose, because a model describing a failure fluently is not
evidence that anything worked.

Nothing here talks to production.
"""

from __future__ import annotations

import httpx

from scripts.check_railway_integration import (
    READ_ONLY_REQUEST,
    Outcome,
    Target,
    railway_executions,
    start_railway_question,
    verdicts,
)

TOKEN = "test-operator-token-not-a-real-credential-0123456789"
RUN_ID = "1a7f813c-b79c-4baf-a5dd-d5da868c9c87"

TARGET = Target(
    api_url="https://api.example/api",
    web_url="https://web.example",
    operator_token=TOKEN,
)


def execution(
    tool_name: str,
    *,
    status: str = "SUCCESS",
    output: dict | None = None,
    error: str | None = None,
    permission: str = "read",
) -> dict:
    return {
        "tool_name": tool_name,
        "service": "railway",
        "status": status,
        "permission": permission,
        "output": output,
        "error": error,
    }


HEALTHY_EXECUTIONS = [
    execution(
        "railway_project_info",
        output={
            "project_id": "9a0d6c51",
            "name": "pacific-recreation",
            "services": [{"id": "s1", "name": "ulugbek-ai-agent"}, {"id": "s2", "name": "rubai-stt"}],
            "environments": [{"id": "e1", "name": "production"}],
        },
    ),
    execution(
        "railway_deployments",
        output={"count": 2, "deployments": [{"id": "d1", "status": "SUCCESS"}]},
    ),
]


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


# --------------------------------------------------------------------------- #
# What is asked
# --------------------------------------------------------------------------- #
def test_the_request_asks_for_nothing_that_changes_anything() -> None:
    """Read-only is a property of the request, not a hope about the model."""
    text = READ_ONLY_REQUEST.lower()
    assert "read-only" in text or "read only" in text
    for forbidden in ("deploy", "restart", "delete", "redeploy", "remove"):
        # The one mention allowed is an instruction not to do it.
        if forbidden in text:
            assert "do not" in text or "never" in text


async def test_the_question_is_sent_as_a_background_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/agent/runs")
        return httpx.Response(202, json={"run_id": RUN_ID, "status": "RUNNING"})

    async with client_for(handler) as http:
        checkpoint, run_id = await start_railway_question(TARGET, http)

    assert checkpoint.outcome is Outcome.OK
    assert run_id == RUN_ID


async def test_a_run_that_will_not_start_yields_no_run_id() -> None:
    async with client_for(lambda request: httpx.Response(503)) as http:
        checkpoint, run_id = await start_railway_question(TARGET, http)

    assert checkpoint.outcome is Outcome.FAILED
    assert run_id is None


async def test_only_this_runs_railway_executions_are_read() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=HEALTHY_EXECUTIONS)

    async with client_for(handler) as http:
        await railway_executions(TARGET, http, RUN_ID)

    assert f"run_id={RUN_ID}" in seen[0]
    assert "service=railway" in seen[0]


# --------------------------------------------------------------------------- #
# The four verdicts
# --------------------------------------------------------------------------- #
def named(checkpoints: list, name: str):
    return next(c for c in checkpoints if c.name == name)


def test_a_working_token_passes_all_four() -> None:
    results = verdicts(HEALTHY_EXECUTIONS)

    for name in (
        "Railway auth",
        "Project access",
        "Services read",
        "Deployments read",
    ):
        assert named(results, name).outcome is Outcome.OK, name


def test_a_missing_token_fails_auth_rather_than_everything_vaguely() -> None:
    executions = [
        execution(
            "railway_project_info",
            status="FAILURE",
            error=(
                "RAILWAY_TOKEN is not set, so Railway cannot be reached. Add it "
                "to the environment and restart the service."
            ),
        )
    ]
    results = verdicts(executions)

    assert named(results, "Railway auth").outcome is Outcome.FAILED
    assert "RAILWAY_TOKEN" in named(results, "Railway auth").detail


def test_a_rejected_token_says_so_and_mentions_the_kind() -> None:
    """A project token sent as a bearer is the mistake this catches."""
    executions = [
        execution(
            "railway_project_info",
            status="FAILURE",
            error="Railway rejected the credential (HTTP 401). Check RAILWAY_TOKEN.",
        )
    ]
    results = verdicts(executions)

    auth = named(results, "Railway auth")
    assert auth.outcome is Outcome.FAILED
    assert "RAILWAY_TOKEN_KIND" in auth.detail


def test_reading_the_project_can_fail_on_its_own() -> None:
    executions = [
        execution("railway_project_info", status="FAILURE", error="project not found"),
        execution("railway_deployments", output={"count": 1, "deployments": []}),
    ]
    results = verdicts(executions)

    # The token clearly worked for the other call, so auth is not the problem.
    assert named(results, "Railway auth").outcome is Outcome.OK
    assert named(results, "Project access").outcome is Outcome.FAILED
    assert named(results, "Services read").outcome is Outcome.FAILED


def test_a_project_with_no_services_is_not_a_services_read() -> None:
    executions = [
        execution(
            "railway_project_info",
            output={"project_id": "9a0d6c51", "name": "p", "services": []},
        )
    ]
    results = verdicts(executions)

    assert named(results, "Project access").outcome is Outcome.OK
    assert named(results, "Services read").outcome is Outcome.FAILED


def test_deployments_that_were_never_read_are_not_a_pass() -> None:
    results = verdicts([HEALTHY_EXECUTIONS[0]])
    assert named(results, "Deployments read").outcome is Outcome.FAILED
    assert "railway_deployments" in named(results, "Deployments read").detail


def test_nothing_ran_at_all_is_reported_as_such() -> None:
    results = verdicts([])

    assert named(results, "Railway auth").outcome is Outcome.FAILED
    assert "no Railway tool ran" in named(results, "Railway auth").detail


# --------------------------------------------------------------------------- #
# The safety rail
# --------------------------------------------------------------------------- #
def test_a_write_operation_having_run_is_a_hard_failure() -> None:
    """The check is read-only, and proving that is part of its job.

    railway_deploy is CRITICAL and therefore always gated behind an approval,
    so this should be impossible. An assertion that something is impossible is
    worth having precisely because it costs nothing until it is wrong.
    """
    executions = HEALTHY_EXECUTIONS + [
        execution("railway_deploy", permission="critical", output={"id": "d9"})
    ]
    results = verdicts(executions)

    safety = named(results, "no write operation ran")
    assert safety.outcome is Outcome.FAILED
    assert "railway_deploy" in safety.detail


def test_read_only_executions_pass_the_safety_rail() -> None:
    assert named(verdicts(HEALTHY_EXECUTIONS), "no write operation ran").outcome is Outcome.OK


def test_the_verdicts_never_contain_the_token() -> None:
    from scripts.monitor_production import Report

    executions = [
        execution("railway_project_info", status="FAILURE", error=f"token {TOKEN} refused")
    ]
    rendered = Report(verdicts(executions)).render()

    assert TOKEN not in rendered
