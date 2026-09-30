#!/usr/bin/env python3
"""Does the deployed backend's Railway token actually work? Read-only.

Nothing outside the API can answer this. `/health` carries no integrations
block, and the Railway tools are registered whether or not a token is set --
deliberately, so a missing one produces a clear message at call time instead of
a silently absent capability. Their presence in `/api/tools` therefore proves
nothing about the credential.

So the check makes the deployed agent use the token, and then reads the
recorded tool executions rather than the prose the agent wrote. A model
describing a failure fluently is not evidence that anything worked; a
`ToolExecution` row with `status=SUCCESS` and a service list in its output is.

Read-only throughout. The request asks only for facts, and `railway_deploy` is
CRITICAL, which the permission system always gates behind a human approval --
so a deploy cannot happen even if the model asked for one. That this held is
checked afterwards rather than assumed.

    python scripts/check_railway_integration.py \\
        --api https://<api>/api --web https://<console>

Exit status 0 when every verdict passes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Final

import httpx

from scripts.monitor_production import (  # noqa: F401 - Outcome is re-exported
    Checkpoint,
    Outcome,
    Report,
    Target,
    _timer,
    check_live_stream,
)

#: What the agent is asked. Facts only, and the read-only instruction is part
#: of the request rather than a hope about how the model will behave.
READ_ONLY_REQUEST: Final[str] = (
    "Using the Railway tools, report read-only facts about this deployment: "
    "the Railway project's name, the services in it, and the status of the "
    "most recent deployments of one of those services. "
    "This is a read-only check: do not deploy, restart, redeploy or change "
    "anything. If a Railway call fails, say exactly what it returned."
)

#: The error text the client produces when no token is configured at all.
_NO_TOKEN: Final[str] = "RAILWAY_TOKEN is not set"


async def start_railway_question(
    target: Target, client: httpx.AsyncClient
) -> tuple[Checkpoint, str | None]:
    """Ask the deployed agent, in the background, the way the console does."""
    elapsed = _timer()
    try:
        response = await client.post(
            f"{target.api_url}/agent/runs",
            json={"message": READ_ONLY_REQUEST, "max_iterations": 8},
            headers=target.auth_header,
            timeout=60.0,
        )
    except httpx.HTTPError as exc:
        return (
            Checkpoint(
                "railway question starts",
                Outcome.FAILED,
                f"could not be reached ({type(exc).__name__})",
                elapsed(),
            ),
            None,
        )

    if response.status_code != 202:
        return (
            Checkpoint(
                "railway question starts",
                Outcome.FAILED,
                f"HTTP {response.status_code}",
                elapsed(),
            ),
            None,
        )
    run_id = response.json().get("run_id")
    if not run_id:
        return (
            Checkpoint(
                "railway question starts",
                Outcome.FAILED,
                "202 without a run id",
                elapsed(),
            ),
            None,
        )
    return (
        Checkpoint("railway question starts", Outcome.OK, "accepted", elapsed()),
        str(run_id),
    )


async def railway_executions(
    target: Target, client: httpx.AsyncClient, run_id: str
) -> list[dict[str, Any]]:
    """Every Railway tool call this run made, as production recorded it.

    Scoped to the run so that an older execution cannot be mistaken for
    evidence about the token being checked now.
    """
    response = await client.get(
        f"{target.api_url}/tools/executions",
        params={"run_id": run_id, "service": "railway", "limit": 50},
        headers=target.auth_header,
        timeout=30.0,
    )
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, list) else []


def _succeeded(execution: dict[str, Any]) -> bool:
    return str(execution.get("status", "")).upper().endswith("SUCCESS")


def _by_name(executions: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [e for e in executions if e.get("tool_name") == name]


def verdicts(executions: list[dict[str, Any]]) -> list[Checkpoint]:
    """The four answers asked for, plus the one that proves nothing was written."""
    results: list[Checkpoint] = []

    # ---------------------------------------------------------- Railway auth #
    if not executions:
        results.append(
            Checkpoint(
                "Railway auth",
                Outcome.FAILED,
                "no Railway tool ran at all, so the token was never used",
                0,
            )
        )
    elif any(_succeeded(e) for e in executions):
        results.append(
            Checkpoint(
                "Railway auth",
                Outcome.OK,
                f"{sum(1 for e in executions if _succeeded(e))} of "
                f"{len(executions)} Railway calls succeeded",
                0,
            )
        )
    else:
        errors = " | ".join(str(e.get("error") or "") for e in executions)
        if _NO_TOKEN in errors:
            detail = "RAILWAY_TOKEN is not set on the deployed service"
        elif "401" in errors or "403" in errors or "credential" in errors.lower():
            detail = (
                "Railway rejected the credential. Check RAILWAY_TOKEN, and that "
                "RAILWAY_TOKEN_KIND matches it: a project token uses its own "
                "header and must not be sent as a bearer."
            )
        else:
            detail = f"every Railway call failed: {errors[:200]}"
        results.append(Checkpoint("Railway auth", Outcome.FAILED, detail, 0))

    # -------------------------------------------------------- Project access #
    info = [e for e in _by_name(executions, "railway_project_info") if _succeeded(e)]
    if info:
        output = info[0].get("output") or {}
        results.append(
            Checkpoint(
                "Project access",
                Outcome.OK,
                f"project {output.get('name')!r} ({output.get('project_id')})",
                0,
            )
        )
    else:
        failed = _by_name(executions, "railway_project_info")
        detail = (
            f"railway_project_info failed: {failed[0].get('error')}"
            if failed
            else "railway_project_info never ran"
        )
        results.append(Checkpoint("Project access", Outcome.FAILED, detail, 0))

    # --------------------------------------------------------- Services read #
    services = ((info[0].get("output") or {}).get("services") or []) if info else []
    if services:
        names = ", ".join(str(s.get("name")) for s in services)
        results.append(
            Checkpoint(
                "Services read", Outcome.OK, f"{len(services)} services: {names}", 0
            )
        )
    else:
        results.append(
            Checkpoint(
                "Services read",
                Outcome.FAILED,
                "no service list came back from railway_project_info",
                0,
            )
        )

    # ------------------------------------------------------ Deployments read #
    deployments = [
        e for e in _by_name(executions, "railway_deployments") if _succeeded(e)
    ]
    if deployments:
        output = deployments[0].get("output") or {}
        results.append(
            Checkpoint(
                "Deployments read",
                Outcome.OK,
                f"{output.get('count')} deployment(s) read",
                0,
            )
        )
    else:
        failed = _by_name(executions, "railway_deployments")
        detail = (
            f"railway_deployments failed: {failed[0].get('error')}"
            if failed
            else "railway_deployments never ran"
        )
        results.append(Checkpoint("Deployments read", Outcome.FAILED, detail, 0))

    # ----------------------------------------------------------- Safety rail #
    # railway_deploy is CRITICAL, and a critical tool is always gated behind a
    # human approval. This asserts that held. It costs nothing until it is
    # wrong, and if it is ever wrong that is the most important line here.
    wrote = [
        e
        for e in executions
        if e.get("tool_name") == "railway_deploy"
        or str(e.get("permission", "")).lower() in ("critical", "execute", "delete")
    ]
    if wrote:
        results.append(
            Checkpoint(
                "no write operation ran",
                Outcome.FAILED,
                "a gated tool executed during a read-only check: "
                + ", ".join(sorted({str(e.get("tool_name")) for e in wrote})),
                0,
            )
        )
    else:
        results.append(
            Checkpoint(
                "no write operation ran", Outcome.OK, "every call was read-only", 0
            )
        )

    return results


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True)
    parser.add_argument("--web", required=True)
    arguments = parser.parse_args(argv)

    target = Target(
        api_url=arguments.api,
        web_url=arguments.web,
        operator_token=os.environ.get("AUTH_TOKEN") or None,
    )
    if not target.operator_token:
        print("AUTH_TOKEN is not set, so the agent cannot be asked anything.")
        return 1

    report = Report()
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        started, run_id = await start_railway_question(target, client)
        report.checkpoints.append(started)
        if run_id is None:
            print(report.render())
            return report.exit_code

        report.checkpoints.append(await check_live_stream(target, client, run_id))

        try:
            executions = await railway_executions(target, client, run_id)
        except httpx.HTTPError as exc:
            report.checkpoints.append(
                Checkpoint(
                    "tool executions readable",
                    Outcome.FAILED,
                    f"could not be read ({type(exc).__name__})",
                    0,
                )
            )
            print(report.render())
            return report.exit_code

        print(f"Railway tool calls recorded for this run: {len(executions)}")
        for execution in executions:
            print(
                f"  {execution.get('tool_name')}: {execution.get('status')}"
                f" ({execution.get('duration_ms')}ms)"
            )
        print()
        report.checkpoints.extend(verdicts(executions))

    print(report.render())
    return report.exit_code


if __name__ == "__main__":  # pragma: no cover - the entrypoint
    sys.exit(asyncio.run(main()))
