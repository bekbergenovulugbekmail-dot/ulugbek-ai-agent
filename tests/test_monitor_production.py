"""The production monitor.

It runs on a schedule against the deployed services, so it has to be right
about two things that pull in opposite directions: it must notice a real
outage, and it must not cry wolf. A monitor that pages on every Railway cold
start gets muted, and a muted monitor is worse than none — the backend was once
down for nine days with nobody looking.

None of this talks to production. The checks take an injected client, which is
what lets a transport double answer exactly the way a broken deployment would.
"""

from __future__ import annotations

import json

import httpx
import pytest

from scripts.monitor_production import (
    Checkpoint,
    Outcome,
    Report,
    Target,
    check_agent_background_run,
    check_api_health,
    check_auth_accepts_the_operator,
    check_auth_refuses_anonymous,
    check_live_stream,
    check_speech_configuration,
    check_speech_service_answers,
    check_web_health,
    run_all,
    silence_wav,
)

TOKEN = "test-operator-token-not-a-real-credential-0123456789"
RUN_ID = "1a7f813c-b79c-4baf-a5dd-d5da868c9c87"

TARGET = Target(
    api_url="https://api.example/api",
    web_url="https://web.example",
    operator_token=TOKEN,
)

HEALTHY = {
    "status": "ok",
    "version": "0.1.0",
    "environment": "production",
    "database": {"connected": True, "error": None},
    "llm": {"configured": True, "model": "claude-opus-5"},
    "auth": {"configured": True, "usable": True},
    "stt": {"provider": "rubai", "configured": True, "usable": True},
    "tools": {"count": 16},
}


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)


def sse(*frames: str) -> httpx.Response:
    return httpx.Response(
        200,
        text="".join(frames),
        headers={"content-type": "text/event-stream"},
    )


def done_frame(status: str = "COMPLETED") -> str:
    return f'event: done\ndata: {json.dumps({"run_id": RUN_ID, "status": status})}\n\n'


# --------------------------------------------------------------------------- #
# The API
# --------------------------------------------------------------------------- #
async def test_a_healthy_api_passes() -> None:
    async with client_for(lambda request: httpx.Response(200, json=HEALTHY)) as http:
        result = await check_api_health(TARGET, http)

    assert result.outcome is Outcome.OK
    assert "0.1.0" in result.detail


@pytest.mark.parametrize("code", [500, 502, 503, 504])
async def test_a_5xx_from_the_api_fails(code: int) -> None:
    async with client_for(lambda request: httpx.Response(code)) as http:
        result = await check_api_health(TARGET, http, attempts=2, backoff_seconds=0.0)

    assert result.outcome is Outcome.FAILED
    assert str(code) in result.detail


async def test_a_single_bad_gateway_is_not_an_outage() -> None:
    """Railway answers 502 for a moment while a container is replaced.

    Paging on that is how a monitor gets muted, so one failure followed by a
    success is a pass — and the detail says it took two goes.
    """
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) == 1:
            return httpx.Response(502)
        return httpx.Response(200, json=HEALTHY)

    async with client_for(handler) as http:
        result = await check_api_health(TARGET, http, attempts=3, backoff_seconds=0.0)

    assert result.outcome is Outcome.OK
    assert "2 attempts" in result.detail


async def test_a_timeout_is_a_failure_not_a_crash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    async with client_for(handler) as http:
        result = await check_api_health(TARGET, http, attempts=2, backoff_seconds=0.0)

    assert result.outcome is Outcome.FAILED
    assert "timed out" in result.detail.lower() or "timeout" in result.detail.lower()


async def test_a_degraded_dependency_is_reported_even_though_it_answered() -> None:
    """HTTP 200 is not health. A database the API cannot reach is an outage."""
    body = HEALTHY | {"database": {"connected": False, "error": "connection refused"}}

    async with client_for(lambda request: httpx.Response(200, json=body)) as http:
        result = await check_api_health(TARGET, http)

    assert result.outcome is Outcome.FAILED
    assert "database" in result.detail


# --------------------------------------------------------------------------- #
# The console
# --------------------------------------------------------------------------- #
async def test_the_console_answers() -> None:
    async with client_for(lambda request: httpx.Response(200, text="ok")) as http:
        result = await check_web_health(TARGET, http)

    assert result.outcome is Outcome.OK


async def test_a_console_that_is_down_fails() -> None:
    async with client_for(lambda request: httpx.Response(503)) as http:
        result = await check_web_health(TARGET, http, attempts=2, backoff_seconds=0.0)

    assert result.outcome is Outcome.FAILED


# --------------------------------------------------------------------------- #
# The credential, from outside
# --------------------------------------------------------------------------- #
async def test_an_anonymous_caller_must_be_refused() -> None:
    async with client_for(lambda request: httpx.Response(401)) as http:
        result = await check_auth_refuses_anonymous(TARGET, http)

    assert result.outcome is Outcome.OK


async def test_an_api_that_answers_an_anonymous_caller_is_an_emergency() -> None:
    """The one check whose failure means the deployment is open to the world."""
    async with client_for(lambda request: httpx.Response(200, json={})) as http:
        result = await check_auth_refuses_anonymous(TARGET, http)

    assert result.outcome is Outcome.FAILED
    assert "anonymous" in result.detail.lower()


async def test_no_authorization_header_is_sent_on_the_anonymous_probe() -> None:
    """A probe that accidentally carries the token proves nothing."""
    headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers)
        return httpx.Response(401)

    async with client_for(handler) as http:
        await check_auth_refuses_anonymous(TARGET, http)

    assert "authorization" not in headers[0]


async def test_the_operator_token_is_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, json={"healthy": True})

    async with client_for(handler) as http:
        result = await check_auth_accepts_the_operator(TARGET, http)

    assert result.outcome is Outcome.OK


async def test_a_refused_operator_token_fails() -> None:
    async with client_for(lambda request: httpx.Response(401)) as http:
        result = await check_auth_accepts_the_operator(
            TARGET, http, attempts=2, backoff_seconds=0.0
        )

    assert result.outcome is Outcome.FAILED


async def test_without_a_token_the_positive_half_cannot_run_and_says_so() -> None:
    """Skipped, not passed. A monitor that checks nothing is not green."""
    blind = Target(api_url=TARGET.api_url, web_url=TARGET.web_url, operator_token=None)

    async with client_for(lambda request: httpx.Response(200)) as http:
        result = await check_auth_accepts_the_operator(blind, http)

    assert result.outcome is Outcome.FAILED
    assert "AUTH_TOKEN" in result.detail


# --------------------------------------------------------------------------- #
# Speech
# --------------------------------------------------------------------------- #
async def test_speech_configuration_is_read_from_health() -> None:
    result = check_speech_configuration(HEALTHY)
    assert result.outcome is Outcome.OK
    assert "rubai" in result.detail


async def test_speech_that_is_configured_but_not_usable_fails() -> None:
    body = HEALTHY | {"stt": {"provider": "rubai", "configured": False, "usable": False}}
    result = check_speech_configuration(body)

    assert result.outcome is Outcome.FAILED
    assert "STT_SERVICE_URL" in result.detail


async def test_the_speech_service_is_probed_through_the_api() -> None:
    """The only liveness signal there is.

    The speech service has no public domain on purpose, so nothing outside
    Railway can reach it. The API can, over the private network, and a
    transcription request is the one thing that proves the two ever spoke.
    """
    sent: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/voice/transcribe")
        assert request.headers["content-type"] == "audio/wav"
        sent.append(request.content)
        return httpx.Response(200, json={"text": "musiqa", "language": "uz"})

    async with client_for(handler) as http:
        result = await check_speech_service_answers(TARGET, http)

    assert result.outcome is Outcome.OK
    assert sent[0].startswith(b"RIFF")


async def test_a_speech_service_that_cannot_be_reached_fails() -> None:
    """502 from the API here means the API could not reach the model."""
    async with client_for(
        lambda request: httpx.Response(502, json={"error": {"message": "no route"}})
    ) as http:
        result = await check_speech_service_answers(
            TARGET, http, attempts=2, backoff_seconds=0.0
        )

    assert result.outcome is Outcome.FAILED
    assert "502" in result.detail


async def test_a_transcript_without_text_is_not_a_working_service() -> None:
    async with client_for(lambda request: httpx.Response(200, json={})) as http:
        result = await check_speech_service_answers(TARGET, http)

    assert result.outcome is Outcome.FAILED


def test_the_probe_audio_is_a_real_wav() -> None:
    audio = silence_wav(0.3)
    assert audio.startswith(b"RIFF")
    assert b"WAVE" in audio[:16]
    # Small enough that a daily probe costs nothing, long enough to decode.
    assert 1_000 < len(audio) < 50_000


# --------------------------------------------------------------------------- #
# A real run, end to end
# --------------------------------------------------------------------------- #
async def test_a_background_run_starts_and_is_running() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        # Read-only by construction: the monitor must never ask the agent to
        # change anything.
        assert "pong" in body["message"].lower()
        return httpx.Response(202, json={"run_id": RUN_ID, "status": "RUNNING"})

    async with client_for(handler) as http:
        result, run_id = await check_agent_background_run(TARGET, http)

    assert result.outcome is Outcome.OK
    assert run_id == RUN_ID


async def test_a_run_that_will_not_start_fails_without_a_run_id() -> None:
    async with client_for(lambda request: httpx.Response(500)) as http:
        result, run_id = await check_agent_background_run(
            TARGET, http, attempts=1, backoff_seconds=0.0
        )

    assert result.outcome is Outcome.FAILED
    assert run_id is None


async def test_the_stream_is_followed_to_a_completed_run() -> None:
    async with client_for(lambda request: sse(done_frame("COMPLETED"))) as http:
        result = await check_live_stream(TARGET, http, RUN_ID)

    assert result.outcome is Outcome.OK
    assert "COMPLETED" in result.detail


async def test_a_run_that_fails_is_reported_as_a_failure() -> None:
    async with client_for(lambda request: sse(done_frame("FAILED"))) as http:
        result = await check_live_stream(TARGET, http, RUN_ID)

    assert result.outcome is Outcome.FAILED
    assert "FAILED" in result.detail


async def test_a_failed_run_is_asked_why_rather_than_just_reported() -> None:
    """"The run failed" is a fact nobody can act on.

    The run row carries the error that stopped it, and a monitor that names the
    checkpoint but not the reason still leaves someone opening a dashboard at
    six in the morning to find out what it already knew.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stream"):
            return sse(done_frame("FAILED"))
        assert request.url.path.endswith(f"/agent/runs/{RUN_ID}")
        return httpx.Response(
            200,
            json={
                "id": RUN_ID,
                "status": "FAILED",
                "error": "Reached the maximum of 2 iterations without an answer.",
            },
        )

    async with client_for(handler) as http:
        result = await check_live_stream(TARGET, http, RUN_ID)

    assert result.outcome is Outcome.FAILED
    assert "maximum of 2 iterations" in result.detail


async def test_a_run_whose_error_cannot_be_read_still_fails_cleanly() -> None:
    """The reason is a bonus. Not getting it must not turn a failure into a crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/stream"):
            return sse(done_frame("FAILED"))
        return httpx.Response(500)

    async with client_for(handler) as http:
        result = await check_live_stream(TARGET, http, RUN_ID)

    assert result.outcome is Outcome.FAILED
    assert "FAILED" in result.detail


async def test_a_completed_run_is_not_interrogated() -> None:
    """Nothing extra is asked of production when the answer is already good."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return sse(done_frame("COMPLETED"))

    async with client_for(handler) as http:
        result = await check_live_stream(TARGET, http, RUN_ID)

    assert result.outcome is Outcome.OK
    assert len(paths) == 1


async def test_a_stream_that_never_settles_is_a_failure() -> None:
    """No `done` frame at all: the run is wedged, or the stream is broken."""
    async with client_for(lambda request: sse(": heartbeat\n\n")) as http:
        result = await check_live_stream(TARGET, http, RUN_ID)

    assert result.outcome is Outcome.FAILED
    assert "done" in result.detail


async def test_the_stream_is_opened_with_the_operator_token() -> None:
    headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers)
        return sse(done_frame())

    async with client_for(handler) as http:
        await check_live_stream(TARGET, http, RUN_ID)

    assert headers[0]["authorization"] == f"Bearer {TOKEN}"


async def test_a_stream_refused_outright_fails() -> None:
    async with client_for(lambda request: httpx.Response(401)) as http:
        result = await check_live_stream(TARGET, http, RUN_ID)

    assert result.outcome is Outcome.FAILED
    assert "401" in result.detail


# --------------------------------------------------------------------------- #
# The report
# --------------------------------------------------------------------------- #
def test_all_green_exits_zero_and_says_nothing_alarming() -> None:
    report = Report(
        [
            Checkpoint("api health", Outcome.OK, "ok", 12),
            Checkpoint("console health", Outcome.OK, "ok", 8),
        ]
    )

    assert report.exit_code == 0
    assert report.failures == []


def test_one_failure_names_the_checkpoint_and_exits_non_zero() -> None:
    report = Report(
        [
            Checkpoint("api health", Outcome.OK, "ok", 12),
            Checkpoint("speech service answers", Outcome.FAILED, "HTTP 502", 40),
            Checkpoint("console health", Outcome.OK, "ok", 8),
        ]
    )

    assert report.exit_code == 1
    assert [check.name for check in report.failures] == ["speech service answers"]
    rendered = report.render()
    assert "speech service answers" in rendered
    assert "HTTP 502" in rendered


def test_a_skipped_checkpoint_does_not_fail_the_run() -> None:
    report = Report([Checkpoint("optional", Outcome.SKIPPED, "not configured", 0)])
    assert report.exit_code == 0


def test_the_report_never_contains_the_token() -> None:
    """The whole report is printed into a public build log.

    Every check carries the operator token, and any of them could put it in a
    message by echoing a URL or a header. This is the check that a future one
    cannot quietly start leaking it.
    """
    report = Report(
        [
            Checkpoint("api health", Outcome.OK, f"Bearer {TOKEN}", 1),
            Checkpoint("auth", Outcome.FAILED, f"token={TOKEN} was refused", 2),
        ]
    )

    rendered = report.render()
    assert TOKEN not in rendered
    assert "REDACTED" in rendered


async def test_run_all_stops_naming_a_dead_api_without_pretending_to_check_more() -> None:
    """When the API is down, everything after it would fail for the same reason.

    Eight red checkpoints that all mean "the API is down" is a worse report
    than one, and it buries which one actually matters.
    """
    async with client_for(lambda request: httpx.Response(503)) as http:
        report = await run_all(TARGET, http, attempts=1, backoff_seconds=0.0)

    assert report.exit_code == 1
    assert report.failures[0].name == "api health"
    # The console is a separate service and is still worth knowing about.
    assert any(check.name == "console health" for check in report.checkpoints)
    # But nothing pretended to run a real agent request against a dead API.
    assert not any("agent" in check.name for check in report.checkpoints
                   if check.outcome is Outcome.FAILED and check.name != "api health")
