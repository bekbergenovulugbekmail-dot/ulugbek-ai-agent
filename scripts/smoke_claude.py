#!/usr/bin/env python
"""Live end-to-end check against the real Claude API.

This is the one thing unit tests cannot do: prove the request shape the agent
sends is accepted by Anthropic and that the loop completes on a real model.

    export ANTHROPIC_API_KEY=sk-ant-...
    python -m scripts.smoke_claude "Which projects do I have?"

It runs one real agent run — planner, executor, tools, verifier — against the
configured database, then prints the trace, the verdict and the token usage.
Exits non-zero if the run does not complete, so it can gate a deployment.

Costs real money: one run is a handful of short calls.
"""

from __future__ import annotations

import asyncio
import sys

from ulugbek_ai.agent.engine import AgentEngine
from ulugbek_ai.agent.schemas import AgentRunRequest
from ulugbek_ai.config.settings import get_settings
from ulugbek_ai.core.errors import UlugbekError
from ulugbek_ai.database.session import Database
from ulugbek_ai.events.service import EventService
from ulugbek_ai.llm.claude import ClaudeClient
from ulugbek_ai.observability.logger import configure_logging
from ulugbek_ai.tools.registry import build_default_registry

DEFAULT_PROMPT = "Which projects do I have, and what is each one for?"


async def main(prompt: str) -> int:
    settings = get_settings()
    configure_logging(level=settings.log_level)

    if settings.anthropic_api_key is None:
        print(
            "ANTHROPIC_API_KEY is not set.\n"
            "Export it, or put it in .env (which is gitignored), then run again.",
            file=sys.stderr,
        )
        return 2

    llm = ClaudeClient.from_settings(settings)
    registry = build_default_registry(settings=settings)
    database = Database(settings)

    print(f"Model    : {llm.model}")
    print(f"Effort   : {settings.claude_effort}  thinking={settings.claude_thinking}")
    print(f"Tools    : {len(registry.list())}")
    print(f"Database : {settings.database_url.split('@')[-1]}")
    print(f"Prompt   : {prompt}\n")

    try:
        async with database.session() as session:
            engine = AgentEngine(
                session, llm=llm, registry=registry, settings=settings
            )
            result = await engine.run(AgentRunRequest(message=prompt))

        async with database.session() as session:
            events = await EventService(session).for_run(result.run_id)
    except UlugbekError as error:
        print(f"\nFAILED: {error.code}: {error.message}", file=sys.stderr)
        return 1
    finally:
        await llm.aclose()
        await database.dispose()

    print("Timeline")
    for event in events.events:
        subject = f" [{event.subject}]" if event.subject else ""
        print(f"  {event.type:24}{subject:22} {event.safe_message[:60]}")

    print(f"\nStatus       : {result.status}")
    print(f"Iterations   : {result.iterations}  replans: {result.replans}")
    print(f"Tools used   : {', '.join(result.tools_used) or 'none'}")
    if result.verification:
        print(
            f"Verification : {result.verification.status} — "
            f"{result.verification.reason}"
        )
    usage = result.token_usage
    print(
        f"Tokens       : in={usage.get('input_tokens', 0)} "
        f"out={usage.get('output_tokens', 0)} "
        f"cache_read={usage.get('cache_read_input_tokens', 0)}"
    )
    if result.approval:
        print(f"Approval     : {result.approval.tool_name} is waiting for a decision")
    print(f"\nAnswer\n------\n{result.output or result.error}")

    ok = result.status.value in {"COMPLETED", "WAITING_APPROVAL"}
    print(f"\n{'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) or DEFAULT_PROMPT
    raise SystemExit(asyncio.run(main(prompt)))
