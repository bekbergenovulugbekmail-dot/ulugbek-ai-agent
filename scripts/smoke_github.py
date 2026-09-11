#!/usr/bin/env python
"""Live check of the GitHub integration.

Runs the real read tools against the real API and prints what came back. Use it
after setting ``GITHUB_TOKEN`` to confirm the credential works and the agent can
actually see a repository.

    python -m scripts.smoke_github owner/repo

Exits non-zero if any call fails, so it can gate a deployment.
"""

from __future__ import annotations

import asyncio
import sys

from ulugbek_ai.config.settings import get_settings
from ulugbek_ai.integrations.github.client import GitHubApiError, GitHubClient
from ulugbek_ai.integrations.github.tools import github_tools
from ulugbek_ai.tools.base import ToolContext


async def main(repository: str) -> int:
    settings = get_settings()
    client = GitHubClient.from_settings(settings)

    print(f"GitHub API : {settings.github_api_url}")
    print(f"Token      : {'configured' if client.authenticated else 'not set (public access only)'}")
    print(f"Repository : {repository}\n")

    context = ToolContext()
    failures = 0
    # The write tool is deliberately excluded: a smoke check must not change
    # anything on someone's repository.
    tools = [tool for tool in github_tools(client=client) if tool.permission.value == "READ"]

    for tool in tools:
        try:
            result = await tool.execute({"repository": repository}, context)
        except Exception as exc:  # noqa: BLE001 - a smoke run reports, never crashes
            print(f"  ✕ {tool.name}: {type(exc).__name__}: {exc}")
            failures += 1
            continue

        if not result.ok:
            print(f"  ✕ {tool.name}: {result.error}")
            failures += 1
            continue

        print(f"  ✓ {tool.name} ({result.duration_ms}ms)")
        output = result.output
        if isinstance(output, dict):
            for key in ("default_branch", "pushed_at", "count", "latest_conclusion"):
                if output.get(key) is not None:
                    print(f"      {key}: {output[key]}")
            for entry in (output.get("commits") or [])[:3]:
                print(f"      {entry['sha']}  {entry['message'][:58]}")
            for entry in (output.get("runs") or [])[:3]:
                print(f"      {entry['name']}: {entry['status']}/{entry['conclusion']}")
            for entry in (output.get("pull_requests") or [])[:3]:
                print(f"      #{entry['number']} {entry['title'][:52]}")

    rate = client.last_rate_limit
    if rate and rate.remaining is not None:
        print(f"\nRate limit remaining: {rate.remaining}/{rate.limit}")

    print(f"\n{'FAILED' if failures else 'OK'}: {len(tools) - failures}/{len(tools)} tools succeeded")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m scripts.smoke_github owner/repo", file=sys.stderr)
        raise SystemExit(2)
    try:
        raise SystemExit(asyncio.run(main(sys.argv[1])))
    except GitHubApiError as error:
        print(f"GitHub error: {error.message}", file=sys.stderr)
        raise SystemExit(1) from error
