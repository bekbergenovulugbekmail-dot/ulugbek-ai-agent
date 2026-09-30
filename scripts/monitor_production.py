#!/usr/bin/env python3
"""Watch the three deployed services, on a schedule, and stay quiet when they
are fine.

This exists because the backend was once down for nine days before anyone
looked. There are three services now, and one of them — the speech service —
has no public domain at all, so "is it up" is not a question a browser can ask.

Two failure modes to avoid, and they pull against each other:

* **Missing an outage.** HTTP 200 is not health: the API answers 200 while its
  database is unreachable, and `stt.usable` is a statement about configuration
  rather than a probe of anything. So each check reads the body, not the code.
* **Crying wolf.** Railway answers 502 for a few seconds while it replaces a
  container. A monitor that pages on that gets muted, and a muted monitor is
  worse than none. Every network check is retried before it is believed.

Nothing here writes to production. The agent request asks for the word "pong"
and nothing else; the speech probe sends a third of a second of silence. Both
leave the rows any request leaves and change no data.

    python scripts/monitor_production.py \\
        --api https://<api>/api --web https://<console>

Exit status is the notification: 0 when everything passed, 1 when a checkpoint
failed. Nothing is emailed on a pass, because a daily green mail is a daily
green mail nobody reads.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import struct
import sys
import time
import wave
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Final

import httpx

#: 16 kHz mono, which is what the speech service converts everything to anyway.
SAMPLE_RATE: Final[int] = 16_000

#: Secrets handed to this process, scrubbed from everything it prints. The
#: report goes into a build log anyone with repository access can read, and a
#: check that echoes a URL or a header would otherwise put a token there. This
#: is a registry rather than a parameter so that a check added later cannot
#: leak one by forgetting to ask.
_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Never print *value* again, wherever it turns up."""
    if value and len(value) >= 8:
        _SECRETS.add(value)


def redact(text: str) -> str:
    for secret in _SECRETS:
        text = text.replace(secret, "REDACTED")
    return text


class Outcome(str, Enum):
    OK = "ok"
    FAILED = "failed"
    #: Could not be run, and that is not by itself an alarm — but a check that
    #: needs a credential the monitor was not given is FAILED, not skipped:
    #: a monitor that checks nothing must not look green.
    SKIPPED = "skipped"


@dataclass(slots=True)
class Checkpoint:
    name: str
    outcome: Outcome
    detail: str
    duration_ms: int

    @property
    def mark(self) -> str:
        return {Outcome.OK: "PASS", Outcome.FAILED: "FAIL", Outcome.SKIPPED: "skip"}[
            self.outcome
        ]


@dataclass(slots=True)
class Report:
    checkpoints: list[Checkpoint] = field(default_factory=list)

    @property
    def failures(self) -> list[Checkpoint]:
        return [c for c in self.checkpoints if c.outcome is Outcome.FAILED]

    @property
    def exit_code(self) -> int:
        return 1 if self.failures else 0

    def render(self) -> str:
        width = max((len(c.name) for c in self.checkpoints), default=10)
        lines = [
            f"  {c.mark}  {c.name.ljust(width)}  {c.duration_ms:>6}ms  {c.detail}"
            for c in self.checkpoints
        ]
        if self.failures:
            lines.append("")
            lines.append(f"{len(self.failures)} checkpoint(s) failed:")
            lines += [f"  - {c.name}: {c.detail}" for c in self.failures]
        else:
            lines.append("")
            lines.append("Every checkpoint passed.")
        return redact("\n".join(lines))


@dataclass(slots=True)
class Target:
    api_url: str
    web_url: str
    operator_token: str | None = None

    def __post_init__(self) -> None:
        self.api_url = self.api_url.rstrip("/")
        self.web_url = self.web_url.rstrip("/")
        register_secret(self.operator_token)

    @property
    def auth_header(self) -> dict[str, str]:
        if not self.operator_token:
            return {}
        return {"Authorization": f"Bearer {self.operator_token}"}


def silence_wav(seconds: float = 0.3) -> bytes:
    """A valid 16 kHz mono WAV, built without calling anything.

    It transcribes as nonsense, which is the point: the probe asks whether the
    two services can speak to each other, not what the model heard.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(struct.pack("<h", 0) * int(SAMPLE_RATE * seconds))
    return buffer.getvalue()


# --------------------------------------------------------------------------- #
# Retrying, once, before believing a failure
# --------------------------------------------------------------------------- #
_RETRYABLE: Final[frozenset[int]] = frozenset({429, 502, 503, 504})


async def _attempt(
    call: Callable[[], Awaitable[httpx.Response]],
    *,
    attempts: int,
    backoff_seconds: float,
) -> tuple[httpx.Response | None, str, int]:
    """Call until it answers something that is not obviously transient.

    Returns the response (or ``None``), a description of what went wrong, and
    how many attempts it took — which is worth reporting, because "passed on
    the second try" is a different sentence from "passed".
    """
    problem = "no attempt was made"
    for attempt in range(1, max(1, attempts) + 1):
        try:
            response = await call()
        except httpx.TimeoutException as exc:
            problem = f"timed out ({type(exc).__name__})"
        except httpx.HTTPError as exc:
            problem = f"could not be reached ({type(exc).__name__})"
        else:
            if response.status_code not in _RETRYABLE:
                return response, "", attempt
            problem = f"HTTP {response.status_code}"
        if attempt < attempts:
            await asyncio.sleep(backoff_seconds)
    return None, problem, max(1, attempts)


def _attempt_note(attempts_used: int) -> str:
    return "" if attempts_used <= 1 else f" (passed after {attempts_used} attempts)"


def _timer() -> Callable[[], int]:
    started = time.monotonic()
    return lambda: int((time.monotonic() - started) * 1000)


# --------------------------------------------------------------------------- #
# The checks
# --------------------------------------------------------------------------- #
async def check_api_health(
    target: Target,
    client: httpx.AsyncClient,
    *,
    attempts: int = 3,
    backoff_seconds: float = 5.0,
) -> Checkpoint:
    """The API answers, and says its dependencies are reachable."""
    elapsed = _timer()
    response, problem, used = await _attempt(
        lambda: client.get(f"{target.api_url}/health"),
        attempts=attempts,
        backoff_seconds=backoff_seconds,
    )
    if response is None:
        return Checkpoint("api health", Outcome.FAILED, problem, elapsed())
    if response.status_code != 200:
        return Checkpoint(
            "api health", Outcome.FAILED, f"HTTP {response.status_code}", elapsed()
        )

    try:
        body: dict[str, Any] = response.json()
    except ValueError:
        return Checkpoint(
            "api health", Outcome.FAILED, "the body was not JSON", elapsed()
        )

    # 200 is the transport saying the process is alive. These are the process
    # saying whether it can do its job.
    broken: list[str] = []
    if body.get("status") != "ok":
        broken.append(f"status={body.get('status')!r}")
    if not (body.get("database") or {}).get("connected"):
        error = (body.get("database") or {}).get("error")
        broken.append(f"database unreachable ({error})")
    if not (body.get("llm") or {}).get("configured"):
        broken.append("no LLM configured")
    if not (body.get("auth") or {}).get("usable"):
        broken.append("AUTH_TOKEN not usable")

    if broken:
        return Checkpoint("api health", Outcome.FAILED, "; ".join(broken), elapsed())

    detail = (
        f"version {body.get('version')} in {body.get('environment')}"
        + _attempt_note(used)
    )
    return Checkpoint("api health", Outcome.OK, detail, elapsed())


async def check_web_health(
    target: Target,
    client: httpx.AsyncClient,
    *,
    attempts: int = 3,
    backoff_seconds: float = 5.0,
) -> Checkpoint:
    """The console is a separate service and fails separately."""
    elapsed = _timer()
    response, problem, used = await _attempt(
        lambda: client.get(f"{target.web_url}/healthz"),
        attempts=attempts,
        backoff_seconds=backoff_seconds,
    )
    if response is None:
        return Checkpoint("console health", Outcome.FAILED, problem, elapsed())
    if response.status_code != 200:
        return Checkpoint(
            "console health", Outcome.FAILED, f"HTTP {response.status_code}", elapsed()
        )
    return Checkpoint("console health", Outcome.OK, "answers" + _attempt_note(used), elapsed())


async def check_auth_refuses_anonymous(
    target: Target, client: httpx.AsyncClient
) -> Checkpoint:
    """The check whose failure is an emergency rather than an outage.

    Deliberately not retried: an API that answers a caller with no credential
    has answered, and a second opinion does not make it less true.
    """
    elapsed = _timer()
    try:
        response = await client.get(f"{target.api_url}/system/overview", headers={})
    except httpx.HTTPError as exc:
        return Checkpoint(
            "auth refuses anonymous",
            Outcome.FAILED,
            f"could not be reached ({type(exc).__name__})",
            elapsed(),
        )

    if response.status_code == 401:
        return Checkpoint(
            "auth refuses anonymous", Outcome.OK, "401 without a token", elapsed()
        )
    detail = (
        "the API answered an anonymous caller with data"
        if response.status_code == 200
        else f"expected 401, got HTTP {response.status_code}"
    )
    return Checkpoint("auth refuses anonymous", Outcome.FAILED, detail, elapsed())


async def check_auth_accepts_the_operator(
    target: Target,
    client: httpx.AsyncClient,
    *,
    attempts: int = 3,
    backoff_seconds: float = 5.0,
) -> Checkpoint:
    """The other half: refusing everyone is not the same as working."""
    elapsed = _timer()
    if not target.operator_token:
        return Checkpoint(
            "auth accepts the operator",
            Outcome.FAILED,
            "no AUTH_TOKEN was given, so the accepted path cannot be checked",
            elapsed(),
        )

    response, problem, used = await _attempt(
        lambda: client.get(
            f"{target.api_url}/system/overview", headers=target.auth_header
        ),
        attempts=attempts,
        backoff_seconds=backoff_seconds,
    )
    if response is None:
        return Checkpoint("auth accepts the operator", Outcome.FAILED, problem, elapsed())
    if response.status_code != 200:
        return Checkpoint(
            "auth accepts the operator",
            Outcome.FAILED,
            f"the operator token was refused with HTTP {response.status_code}",
            elapsed(),
        )
    return Checkpoint(
        "auth accepts the operator", Outcome.OK, "200" + _attempt_note(used), elapsed()
    )


def check_speech_configuration(health_body: dict[str, Any]) -> Checkpoint:
    """What the API believes about speech. Configuration, not liveness."""
    stt = health_body.get("stt") or {}
    provider = stt.get("provider")
    if provider == "disabled":
        return Checkpoint(
            "speech configuration", Outcome.SKIPPED, "STT_PROVIDER=disabled", 0
        )
    if not stt.get("usable"):
        missing = (
            "STT_SERVICE_URL (and STT_SERVICE_TOKEN)"
            if provider == "rubai"
            else "STT_API_KEY"
        )
        return Checkpoint(
            "speech configuration",
            Outcome.FAILED,
            f"provider {provider!r} is not usable; set {missing}",
            0,
        )
    return Checkpoint(
        "speech configuration", Outcome.OK, f"provider {provider!r} usable", 0
    )


async def check_speech_service_answers(
    target: Target,
    client: httpx.AsyncClient,
    *,
    attempts: int = 2,
    backoff_seconds: float = 5.0,
    timeout_seconds: float = 180.0,
) -> Checkpoint:
    """Does the speech service actually answer?

    It has no public domain, so nothing out here can ask it directly. The API
    can, over Railway's private network, and a transcription is the only thing
    that proves the two ever spoke — `stt.usable` is a statement about
    configuration and would be true with the speech service switched off.

    The audio is a third of a second of silence, which comes back as nonsense.
    That is fine: this asks whether the round trip works, not what was heard.
    """
    elapsed = _timer()
    audio = silence_wav(0.3)
    response, problem, used = await _attempt(
        lambda: client.post(
            f"{target.api_url}/voice/transcribe",
            content=audio,
            headers={**target.auth_header, "Content-Type": "audio/wav"},
            timeout=timeout_seconds,
        ),
        attempts=attempts,
        backoff_seconds=backoff_seconds,
    )
    if response is None:
        return Checkpoint("speech service answers", Outcome.FAILED, problem, elapsed())
    if response.status_code != 200:
        return Checkpoint(
            "speech service answers",
            Outcome.FAILED,
            f"HTTP {response.status_code} from the API's transcribe endpoint",
            elapsed(),
        )
    try:
        body = response.json()
    except ValueError:
        body = {}
    if "text" not in body:
        return Checkpoint(
            "speech service answers",
            Outcome.FAILED,
            "answered 200 without a transcript",
            elapsed(),
        )
    return Checkpoint(
        "speech service answers",
        Outcome.OK,
        "round trip through the private network" + _attempt_note(used),
        elapsed(),
    )


#: What the agent is asked. One word back, no tools, nothing to change.
SMOKE_MESSAGE: Final[str] = (
    "Reply with the single word: pong. Do not use any tools."
)


async def check_agent_background_run(
    target: Target,
    client: httpx.AsyncClient,
    *,
    attempts: int = 2,
    backoff_seconds: float = 5.0,
) -> tuple[Checkpoint, str | None]:
    """Start a run the way the console does, and get its id back."""
    elapsed = _timer()
    response, problem, used = await _attempt(
        lambda: client.post(
            f"{target.api_url}/agent/runs",
            json={"message": SMOKE_MESSAGE, "max_iterations": 2},
            headers=target.auth_header,
            timeout=60.0,
        ),
        attempts=attempts,
        backoff_seconds=backoff_seconds,
    )
    if response is None:
        return Checkpoint("agent run starts", Outcome.FAILED, problem, elapsed()), None
    if response.status_code != 202:
        return (
            Checkpoint(
                "agent run starts",
                Outcome.FAILED,
                f"HTTP {response.status_code}",
                elapsed(),
            ),
            None,
        )
    body = response.json()
    run_id = body.get("run_id")
    if not run_id:
        return (
            Checkpoint(
                "agent run starts", Outcome.FAILED, "202 without a run id", elapsed()
            ),
            None,
        )
    return (
        Checkpoint(
            "agent run starts",
            Outcome.OK,
            f"status {body.get('status')}" + _attempt_note(used),
            elapsed(),
        ),
        str(run_id),
    )


async def check_live_stream(
    target: Target,
    client: httpx.AsyncClient,
    run_id: str,
    *,
    timeout_seconds: float = 180.0,
) -> Checkpoint:
    """Follow the run on SSE until it settles.

    The stream replays from the beginning, so a run that finished before this
    opened still produces its `done` frame — which removes the race that would
    otherwise make this check flaky for fast runs.

    An operator holding the real token may open the stream with a header; the
    console cannot, which is why the stream-token endpoint exists. Using the
    header here keeps the monitor to one credential.
    """
    elapsed = _timer()
    url = f"{target.api_url}/events/runs/{run_id}/stream"
    status: str | None = None
    saw_event = False
    try:
        async with client.stream(
            "GET", url, headers=target.auth_header, timeout=timeout_seconds
        ) as response:
            if response.status_code != 200:
                return Checkpoint(
                    "live stream",
                    Outcome.FAILED,
                    f"HTTP {response.status_code}",
                    elapsed(),
                )
            pending: str | None = None
            async for line in response.aiter_lines():
                line = line.strip()
                if line.startswith("event:"):
                    pending = line.split(":", 1)[1].strip()
                    saw_event = True
                elif line.startswith("data:") and pending == "done":
                    try:
                        status = json.loads(line.split(":", 1)[1].strip()).get("status")
                    except ValueError:
                        status = None
                    break
    except httpx.HTTPError as exc:
        return Checkpoint(
            "live stream",
            Outcome.FAILED,
            f"the stream broke ({type(exc).__name__})",
            elapsed(),
        )

    if status is None:
        detail = (
            "the stream closed without a done frame"
            if saw_event
            else "no done frame and no events at all"
        )
        return Checkpoint("live stream", Outcome.FAILED, detail, elapsed())
    if status != "COMPLETED":
        # "The run failed" is a fact nobody can act on. The row carries the
        # error that stopped it, and asking costs one request on a path that
        # has already gone wrong.
        reason = await _run_error(target, client, run_id)
        detail = f"the run settled as {status}"
        return Checkpoint(
            "live stream", Outcome.FAILED, f"{detail}: {reason}" if reason else detail,
            elapsed(),
        )
    return Checkpoint("live stream", Outcome.OK, "run COMPLETED", elapsed())


async def _run_error(
    target: Target, client: httpx.AsyncClient, run_id: str
) -> str | None:
    """Why the run stopped, if production will say. Never raises."""
    try:
        response = await client.get(
            f"{target.api_url}/agent/runs/{run_id}",
            headers=target.auth_header,
            timeout=30.0,
        )
        if response.status_code != 200:
            return None
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    error = body.get("error")
    return str(error) if error else None


# --------------------------------------------------------------------------- #
# All of it
# --------------------------------------------------------------------------- #
async def run_all(
    target: Target,
    client: httpx.AsyncClient,
    *,
    attempts: int = 3,
    backoff_seconds: float = 5.0,
    include_agent_run: bool = True,
    include_speech_probe: bool = True,
) -> Report:
    """Every checkpoint, in an order that keeps the report readable.

    When the API is down, everything behind it fails for that one reason. Eight
    red lines that all mean "the API is down" bury the one that matters, so the
    dependent checks are skipped and say why.
    """
    report = Report()
    retry = {"attempts": attempts, "backoff_seconds": backoff_seconds}

    api = await check_api_health(target, client, **retry)
    report.checkpoints.append(api)

    # A separate service: worth knowing about even when the API is down.
    report.checkpoints.append(await check_web_health(target, client, **retry))

    if api.outcome is not Outcome.OK:
        for name in (
            "auth refuses anonymous",
            "auth accepts the operator",
            "speech configuration",
            "speech service answers",
            "agent run starts",
            "live stream",
        ):
            report.checkpoints.append(
                Checkpoint(name, Outcome.SKIPPED, "the API is not healthy", 0)
            )
        return report

    report.checkpoints.append(await check_auth_refuses_anonymous(target, client))
    report.checkpoints.append(
        await check_auth_accepts_the_operator(target, client, **retry)
    )

    health_body: dict[str, Any] = {}
    try:
        health_body = (await client.get(f"{target.api_url}/health")).json()
    except (httpx.HTTPError, ValueError):  # pragma: no cover - api health just passed
        health_body = {}
    speech = check_speech_configuration(health_body)
    report.checkpoints.append(speech)

    if include_speech_probe and speech.outcome is Outcome.OK:
        report.checkpoints.append(
            await check_speech_service_answers(
                target, client, attempts=min(attempts, 2), backoff_seconds=backoff_seconds
            )
        )
    else:
        report.checkpoints.append(
            Checkpoint(
                "speech service answers",
                Outcome.SKIPPED,
                "speech is not configured" if speech.outcome is not Outcome.OK
                else "not requested",
                0,
            )
        )

    if not include_agent_run:
        for name in ("agent run starts", "live stream"):
            report.checkpoints.append(
                Checkpoint(name, Outcome.SKIPPED, "not requested", 0)
            )
        return report

    started, run_id = await check_agent_background_run(
        target, client, attempts=min(attempts, 2), backoff_seconds=backoff_seconds
    )
    report.checkpoints.append(started)
    if run_id is None:
        report.checkpoints.append(
            Checkpoint("live stream", Outcome.SKIPPED, "no run to follow", 0)
        )
        return report

    report.checkpoints.append(await check_live_stream(target, client, run_id))
    return report


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True, help="API base URL, including /api")
    parser.add_argument("--web", required=True, help="Console base URL")
    parser.add_argument(
        "--no-agent-run",
        action="store_true",
        help="Skip the real agent request (it spends tokens).",
    )
    parser.add_argument(
        "--no-speech-probe",
        action="store_true",
        help="Skip the transcription round trip.",
    )
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--backoff-seconds", type=float, default=5.0)
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> int:
    arguments = _parse(argv)
    target = Target(
        api_url=arguments.api,
        web_url=arguments.web,
        operator_token=os.environ.get("AUTH_TOKEN") or None,
    )
    print(f"API     {target.api_url}")
    print(f"Console {target.web_url}")
    print()

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        report = await run_all(
            target,
            client,
            attempts=arguments.attempts,
            backoff_seconds=arguments.backoff_seconds,
            include_agent_run=not arguments.no_agent_run,
            include_speech_probe=not arguments.no_speech_probe,
        )

    print(report.render())
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover - the entrypoint
    sys.exit(asyncio.run(main()))
