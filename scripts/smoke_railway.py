#!/usr/bin/env python
"""Live check of the Railway integration.

Runs the real read tools against the real API and prints what came back. Use it
after setting ``RAILWAY_TOKEN`` to confirm the credential works, and to find the
service and environment ids a deploy needs.

    python -m scripts.smoke_railway                 # uses the configured ids
    python -m scripts.smoke_railway <project_id>    # or an explicit project

**It never deploys.** Only READ tools are run, so this cannot change what is
running. Exits non-zero if any call fails.
"""

from __future__ import annotations

import asyncio
import sys

from ulugbek_ai.config.settings import get_settings
from ulugbek_ai.core.enums import PermissionLevel
from ulugbek_ai.integrations.railway.client import RailwayApiError, RailwayClient
from ulugbek_ai.integrations.railway.tools import railway_tools
from ulugbek_ai.tools.base import ToolContext


async def main(project_id: str | None) -> int:
    settings = get_settings()
    client = RailwayClient.from_settings(settings)
    project_id = project_id or settings.railway_project_id

    print(f"Railway API : {settings.railway_api_url}")
    print(f"Token       : {'configured' if client.authenticated else 'NOT SET'}")
    print(f"Token kind  : {settings.railway_token_kind}")
    print(f"Project     : {project_id or 'not configured'}\n")

    if not client.authenticated:
        print("Set RAILWAY_TOKEN in .env, then run this again.")
        return 1

    try:
        me = await client.whoami()
        print(f"Authenticated as: {me.get('name') or me.get('id')}\n")
    except RailwayApiError as exc:
        # A project token cannot read the account; that is not a failure.
        print(f"whoami: {exc.message}\n")

    context = ToolContext()
    arguments = {"project_id": project_id} if project_id else {}
    failures = 0

    # The deploy tool is deliberately excluded: a smoke check must never
    # change what is running for real users.
    tools = [
        tool
        for tool in railway_tools(
            client=client,
            project_id=settings.railway_project_id,
            environment_id=settings.railway_environment_id,
            service_id=settings.railway_service_id,
        )
        if tool.permission is PermissionLevel.READ
        # The log reader needs a deployment id, which this script does not
        # have until it has already listed deployments.
        and tool.name != "railway_deployment_logs"
    ]

    for tool in tools:
        try:
            result = await tool.execute(dict(arguments), context)
        except Exception as exc:  # noqa: BLE001 - a smoke run reports, never crashes
            print(f"  ✕ {tool.name}: {type(exc).__name__}: {exc}")
            failures += 1
            continue

        if not result.ok:
            print(f"  ✕ {tool.name}: {result.error}")
            failures += 1
            continue

        print(f"  ✓ {tool.name} ({result.duration_ms}ms)")
        output = result.output if isinstance(result.output, dict) else {}
        for entry in (output.get("projects") or [])[:5]:
            print(f"      project     {entry['project_id']}  {entry.get('name')}")
        for entry in (output.get("services") or [])[:10]:
            print(f"      service     {entry['id']}  {entry.get('name')}")
        for entry in (output.get("environments") or [])[:10]:
            print(f"      environment {entry['id']}  {entry.get('name')}")
        for entry in (output.get("deployments") or [])[:5]:
            print(
                f"      deployment  {entry['deployment_id']}  "
                f"{entry.get('status')} ({entry.get('outcome')})"
            )

    print(
        f"\n{'FAILED' if failures else 'OK'}: "
        f"{len(tools) - failures}/{len(tools)} tools succeeded"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    argument = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        raise SystemExit(asyncio.run(main(argument)))
    except RailwayApiError as error:
        print(f"Railway error: {error.message}", file=sys.stderr)
        raise SystemExit(1) from error
