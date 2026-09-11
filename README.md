# ULUGBEK AI

A universal, modular, extensible **AI agent core** — not a chatbot.

The agent understands a request, loads only the context that matters, plans,
picks and runs tools under a permission policy, checks its own work, and stops
to ask a human before doing anything dangerous.

> **Phase 1 scope.** This repository contains the agent core only. ERP,
> Telegram, Instagram, GitHub, Railway and browser integrations are later
> phases. Every one of them plugs in as a `Tool` subclass and a `Project`
> integration binding — no change to the engine, the permission system or the
> API is required. The extension points are real and exercised by tests, not
> TODOs.

---

## Table of contents

- [The agent loop](#the-agent-loop)
- [Architecture](#architecture)
- [Installation](#installation)
- [Environment variables](#environment-variables)
- [Local development](#local-development)
- [Database and migrations](#database-and-migrations)
- [Tests](#tests)
- [API](#api)
- [Memory](#memory)
- [Tools](#tools)
- [Permissions](#permissions)
- [Approvals](#approvals)
- [Verification](#verification)
- [Logging and audit](#logging-and-audit)
- [Security](#security)
- [Railway deployment](#railway-deployment)
- [Extending the agent](#extending-the-agent)

---

## The agent loop

```
USER
 │
 ├─ UNDERSTAND      resolve the user, route the request to a project
 ├─ LOAD CONTEXT    retrieve only relevant memory, under a character budget
 ├─ PLAN            produce a JSON-schema-checked plan with expected outcomes
 │
 ├─ SELECT TOOL ◄──────────────────────┐
 ├─ EXECUTE         permission check ──┤ gated? → WAITING_APPROVAL → human
 ├─ OBSERVE         record the result  │
 ├─ REASON          decide: continue or answer
 │                                     │
 ├─ VERIFY          evidence-based, sceptical
 │   ├─ SUCCESS → COMPLETE             │
 │   └─ FAILURE → REPLAN ──────────────┘
 │
 └─ COMPLETE
```

Three properties the loop guarantees:

| Property | How |
|---|---|
| **It terminates** | iteration cap, replan cap, wall-clock deadline checked between iterations, per-tool timeouts, LLM request timeout |
| **It is resumable** | the transcript, observations and any half-finished tool phase live on the `agent_runs` row, so an approved run continues in any process |
| **It does not lie** | a final answer is verified against tool evidence before the task is marked `COMPLETED`; a rejected answer triggers a replan |

---

## Architecture

Dependencies point in one direction. `core` knows nothing about anything else;
the agent knows nothing about SQL or the Anthropic SDK.

```
                 ┌──────────┐
                 │   api    │  thin routes, error translation, auth seam
                 └────┬─────┘
                      │
                 ┌────▼─────┐
                 │  agent   │  context · planner · executor · verifier · engine
                 └────┬─────┘
        ┌─────────────┼──────────────┬───────────────┐
   ┌────▼────┐   ┌────▼────┐   ┌─────▼────┐   ┌──────▼─────┐
   │ memory  │   │  tasks  │   │  tools   │   │ approvals  │
   │projects │   │identity │   │registry  │   │            │
   └────┬────┘   └────┬────┘   │permission│   └──────┬─────┘
        │             │        └─────┬────┘          │
        └─────────────┴──────────────┴───────────────┘
                      │
        ┌─────────────┴──────────────┐
   ┌────▼─────┐  ┌─────▼─────┐  ┌────▼──────────┐
   │ database │  │    llm    │  │ observability │
   └────┬─────┘  └─────┬─────┘  └────┬──────────┘
        └──────────────┴─────────────┘
                       │
              ┌────────▼────────┐
              │  core · config  │  enums · errors · redaction
              └─────────────────┘
```

Every domain follows the same three layers:

- **model** — the ORM table,
- **repository** — all SQL for that table, and nothing else,
- **manager** — business rules, the only thing the agent and API call.

```
ulugbek_ai/
├── core/            enums, error hierarchy, secret redaction, time/id helpers
├── config/          typed settings loaded from the environment
├── database/        engine, session scope, portable column types, model registry
├── llm/             provider-agnostic interface + ClaudeClient + scripted double
├── identity/        users (authentication is a later phase; the seam exists)
├── projects/        universal project model + request→project routing
├── memory/          typed long-term memory + pluggable retrieval strategy
├── tasks/           task model + lifecycle state machine + plan bookkeeping
├── tools/           Tool interface, registry, permission system, built-ins
├── approvals/       human-in-the-loop approval lifecycle
├── agent/           context · planner · executor · verifier · engine + trace models
├── observability/   redacting logger + durable audit trail
├── api/             routes, dependencies, error handlers
└── main.py          application factory
```

**Technology:** Python 3.11 · FastAPI · SQLAlchemy 2.0 (async) · Alembic ·
PostgreSQL · Pydantic v2 · Anthropic SDK · Docker · Railway. Async throughout.

---

## Installation

```bash
git clone <repository-url> ulugbek-ai-agent
cd ulugbek-ai-agent

python3.11 -m venv .venv
source .venv/bin/activate

pip install -r requirements-dev.txt   # or requirements.txt for runtime only

cp .env.example .env                  # then set ANTHROPIC_API_KEY
```

---

## Environment variables

Copy `.env.example` to `.env`. **`.env` is gitignored and must never be
committed.** Secrets are read from the environment only.

### Required

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key. Without it the service still starts and serves every non-agent endpoint; `/api/agent/run` returns a clear configuration error. |
| `DATABASE_URL` | PostgreSQL URL. `postgres://` and `postgresql://` are upgraded to `postgresql+asyncpg://` automatically, so Railway's injected value works unchanged. |

### Application

| Variable | Default | Description |
|---|---|---|
| `APP_NAME` | `Ulugbek AI` | Shown in the OpenAPI docs |
| `ENVIRONMENT` | `local` | `local` · `test` · `staging` · `production` |
| `DEBUG` | `false` | Enables autoreload in the dev server |
| `LOG_LEVEL` | `INFO` | Standard logging level |
| `LOG_JSON` | `false` | One JSON object per log line — use in production |
| `API_HOST` / `API_PORT` | `0.0.0.0` / `8000` | Dev server bind address |
| `API_PREFIX` | `/api` | Prefix for every route |
| `CORS_ORIGINS` | `["*"]` | JSON list of allowed origins |

### Database

| Variable | Default |
|---|---|
| `DB_ECHO` | `false` |
| `DB_POOL_SIZE` | `5` |
| `DB_MAX_OVERFLOW` | `10` |

### Claude

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_MODEL` | `claude-opus-5` | Model id |
| `CLAUDE_MAX_TOKENS` | `16000` | Output cap |
| `CLAUDE_EFFORT` | `high` | `low` · `medium` · `high` · `xhigh` · `max` |
| `CLAUDE_THINKING` | `true` | Adaptive extended thinking |
| `CLAUDE_TIMEOUT_SECONDS` | `120` | Per-request timeout |
| `CLAUDE_MAX_RETRIES` | `2` | SDK-level retries |

### Agent loop

| Variable | Default | Description |
|---|---|---|
| `AGENT_MAX_ITERATIONS` | `8` | Hard cap on loop iterations |
| `AGENT_MAX_REPLANS` | `2` | Hard cap on replans after failed verification |
| `AGENT_RUN_TIMEOUT_SECONDS` | `300` | Wall-clock budget for one run |
| `AGENT_VERIFICATION_ENABLED` | `true` | Turning this off is a debugging aid, not a production setting |

### Tools and memory

| Variable | Default | Description |
|---|---|---|
| `TOOL_DEFAULT_TIMEOUT_SECONDS` | `30` | Used when a tool declares none |
| `TOOL_MAX_RESULT_CHARS` | `8000` | Tool output is truncated to this |
| `MEMORY_CONTEXT_LIMIT` | `20` | Max memories loaded per run |
| `MEMORY_CONTEXT_MAX_CHARS` | `12000` | Character budget for retrieved memory |

### Permissions

`auto` · `approval` · `deny`, one per level.

| Variable | Default |
|---|---|
| `PERMISSION_READ` | `auto` |
| `PERMISSION_WRITE` | `auto` |
| `PERMISSION_EXECUTE` | `approval` |
| `PERMISSION_DELETE` | `approval` |
| `PERMISSION_CRITICAL` | `approval` |

> `PERMISSION_CRITICAL=auto` is **silently corrected to `approval`**. A critical
> action can be denied outright, but it can never become automatic.

---

## Local development

### With Docker (recommended)

```bash
cp .env.example .env      # set ANTHROPIC_API_KEY
docker compose up --build
```

Brings up PostgreSQL and the API, applies migrations, and serves
<http://localhost:8000/docs>.

### Without Docker

```bash
# a PostgreSQL instance must be reachable at DATABASE_URL
alembic upgrade head
python -m ulugbek_ai
```

---

## Database and migrations

Eight tables:

| Table | Purpose |
|---|---|
| `users` | owner of projects, tasks and memories |
| `projects` | universal project + non-secret integration bindings |
| `tasks` | goal, status, plan, current step, result |
| `memories` | typed, scoped long-term memory |
| `agent_runs` | one loop invocation: transcript, usage, status |
| `agent_steps` | the execution trace, one row per event |
| `tool_executions` | every tool call, its result and its verification |
| `approvals` | gated actions awaiting a human decision |

```bash
alembic upgrade head                          # apply
alembic revision --autogenerate -m "message"  # create after a model change
alembic check                                 # fail if models and schema differ
alembic downgrade -1                          # roll back one revision
```

The URL comes from application settings, **not** from `alembic.ini` — no
credential is ever committed. `alembic/env.py` imports every model through
`ulugbek_ai/database/registry.py`, so autogenerate cannot miss a table.

---

## Tests

```bash
pytest                    # full suite
pytest -v                 # verbose
pytest tests/test_agent_engine.py
```

Tests run against **in-memory SQLite** by default so the suite is fast and needs
no server. To run the identical suite against the production engine:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://ulugbek:ulugbek@localhost:5432/ulugbek_ai_test pytest
```

No test touches the network: the Anthropic SDK is replaced by a stub and the
agent is driven by `ScriptedLLMClient`, which replays queued responses and
records the requests it received.

Covered: redaction · permissions (including the CRITICAL invariant) · tool
registry (validation, timeouts, crash isolation, output redaction) · built-in
tools (including calculator sandbox escapes) · memory (relevance, scoping,
budgeting) · projects and routing · task lifecycle · verification · approvals ·
the Claude adapter (request shape, response normalization, error translation) ·
the full agent loop (tool use, replanning, iteration cap, approval pause and
resume) · the HTTP surface.

---

## API

Interactive documentation: `/docs`. All routes are under `API_PREFIX`
(default `/api`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness, database probe, tool count |
| `GET` | `/api/health/tools` | Every registered tool with its permission level |
| `POST` | `/api/agent/run` | Run the agent |
| `POST` | `/api/agent/runs/{id}/resume` | Resume a run paused for approval |
| `GET` | `/api/agent/runs` | List runs |
| `GET` | `/api/agent/runs/{id}` | A run with its full execution trace |
| `GET` `POST` | `/api/projects` | List / create projects |
| `GET` `PATCH` | `/api/projects/{id}` | Read / update a project |
| `GET` `POST` | `/api/tasks` | List / create tasks |
| `GET` | `/api/tasks/{id}` | Read a task |
| `POST` | `/api/tasks/{id}/cancel` | Cancel a task |
| `GET` `POST` | `/api/memory` | List / store memories |
| `GET` | `/api/memory/search` | Relevance search |
| `GET` `PATCH` `DELETE` | `/api/memory/{id}` | Read / update / delete a memory |
| `GET` | `/api/approvals` | List approvals |
| `GET` | `/api/approvals/{id}` | Read an approval |
| `POST` | `/api/approvals/{id}/approve` | Approve **and resume the run** |
| `POST` | `/api/approvals/{id}/reject` | Reject **and resume the run** |

Errors always have the same shape:

```json
{ "error": { "code": "not_found", "message": "Project ... not found.", "details": {} } }
```

Example:

```bash
curl -X POST http://localhost:8000/api/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"message": "Which projects do I have?"}'
```

A run that needs approval answers with `status: "WAITING_APPROVAL"` and the
approval to decide — and with `output: null`, because nothing has been claimed:

```json
{
  "run_id": "...",
  "status": "WAITING_APPROVAL",
  "output": null,
  "approval": {
    "approval_id": "...",
    "tool_name": "deploy",
    "permission": "CRITICAL",
    "reason": "CRITICAL actions require explicit human approval.",
    "tool_arguments": {"environment": "production"}
  }
}
```

Approving continues the very same run:

```bash
curl -X POST http://localhost:8000/api/approvals/<approval_id>/approve \
  -H 'Content-Type: application/json' -d '{"decided_by": "ulugbek"}'
```

**Authentication** is a deliberate seam, not an omission: every protected route
depends on `require_principal()` in `ulugbek_ai/api/deps.py`. Replacing that one
function enforces real credentials across the API without touching a route.

---

## Memory

Memory is **not** chat history. Each row is a typed, scoped, individually
retrievable unit:

`USER_CONTEXT` · `PROJECT_CONTEXT` · `DECISION` · `PREFERENCE` · `FACT` ·
`TASK_CONTEXT` · `CONVERSATION_SUMMARY` · `TOOL_RESULT`

`MemoryManager` offers `save_memory` · `search_memory` · `get_memory` ·
`update_memory` · `delete_memory` · `summarize_memory`.

**The whole memory database is never sent to the model.** Context assembly runs
two bounded passes — relevance (terms matching the request) and durability
(standing project/user context, preferences, decisions) — merges them,
de-duplicates, ranks, and cuts to `MEMORY_CONTEXT_MAX_CHARS`. There is a test
that fails if unrelated memories reach the prompt.

Retrieval is pluggable. The default `KeywordSearchStrategy` scores term overlap
with importance and recency decay, and credits prefix matches so *"invoice"*
finds *"invoices"* and *"Railway"* finds *"Railwayda"* — Uzbek is agglutinative,
so suffixed forms are the common case. A future embedding-based strategy just
implements `MemorySearchStrategy`; the `embedding` column is already there.

---

## Tools

A tool is the agent's only way to affect the outside world. Each one declares:

| Field | Meaning |
|---|---|
| `name` | unique, snake_case — what the model calls |
| `description` | written for the model |
| `input_schema` | JSON Schema, validated before execution |
| `output_schema` | JSON Schema of the result |
| `permission` | `READ` · `WRITE` · `EXECUTE` · `DELETE` · `CRITICAL` |
| `timeout_seconds` | hard ceiling |
| `idempotent` | whether the tool may be retried |
| `execute()` | the work |
| `verify()` | optional post-condition check |

`ToolRegistry.execute()` uniformly resolves the tool, validates arguments,
enforces permissions, applies the timeout, converts *any* exception into a
failed result (a tool can never crash a run), and redacts and truncates the
output.

Built-ins in Phase 1 are local and safe:

| Tool | Permission | Description |
|---|---|---|
| `current_time` | READ | Current date/time in any IANA timezone |
| `calculate` | READ | Arithmetic, evaluated over an allow-listed AST — no `eval`, no calls, no imports, no attribute access |
| `memory_search` | READ | Search long-term memory |
| `memory_write` | WRITE | Store a durable fact; `verify()` re-reads the row to prove the write landed |
| `project_list` | READ | List known projects |

---

## Permissions

| Level | Default mode | Meaning |
|---|---|---|
| `READ` | auto | observe only |
| `WRITE` | auto | changes state that is easy to undo |
| `EXECUTE` | approval | runs something with side effects |
| `DELETE` | approval | destroys data |
| `CRITICAL` | **always approval** | production deploys, publishing, irreversible actions |

Modes are `auto` (run now), `approval` (pause for a human) and `deny` (never).
Per-tool overrides exist but can only ever make a tool **stricter** — an override
cannot unlock what the level gates.

---

## Approvals

When the policy gates an action the run does not fail and does not skip the step.
It persists an `Approval` (with **redacted** arguments, because a human reads
it), moves the task to `WAITING_APPROVAL`, and returns.

The hard part is handled correctly: the model may request several tools in one
turn, and the provider requires every `tool_use` block to receive a matching
`tool_result`. Results computed before the gated call are parked on the run, so
on resume the already-executed calls are replayed from that record while only
the approved call actually runs — **work is never repeated and the transcript is
always well-formed.** There is a test for exactly this.

Rejecting does not kill the run either: the agent is told the action was refused
and decides how to proceed, which is normally to explain the situation.

---

## Verification

An action is never assumed to have worked. Two layers run, cheapest first:

1. **Structural** — `ToolOutcomeCheck` rejects an answer that claims an effect
   (in English *or* Uzbek) when every tool call failed. Costs nothing.
2. **Semantic** — `LLMJudgeCheck` compares goal, plan, tool evidence and the
   proposed answer, returning `SUCCESS` / `FAILURE` / `INCONCLUSIVE` with a reason.

A `FAILURE` triggers a replan rather than a confident wrong answer. Later phases
register service-specific checks (a GitHub commit exists, a Railway deployment
is live, a Telegram message was delivered) by implementing `VerificationCheck`.

---

## Logging and audit

Two complementary records.

**Operational logs** pass through a redaction filter, so a credential cannot
reach a log even if one is passed in by mistake. `LOG_JSON=true` gives one JSON
object per line.

**The audit trail** is `agent_steps`: an ordered, durable trace of every run —
`agent_run` · `plan` · `replan` · `llm_call` · `tool_call` · `tool_result` ·
`permission` · `approval` · `verification` · `error` · `final_result`. Plus
`tool_executions`, which records every invocation with its arguments, result,
duration and verification verdict. Read a whole run back with
`GET /api/agent/runs/{id}`.

Every audit payload is redacted on the way in.

---

## Security

- **Secrets come only from the environment.** `ANTHROPIC_API_KEY` is held as a
  `SecretStr`, so even a `repr()` of the settings object cannot leak it.
- **`.env` is gitignored**; only `.env.example` is committed.
- **Redaction** runs on logs, audit rows, tool output, stored memories, task
  errors and approval payloads — both by key name (`api_key`, `password`,
  `token`, …) and by value shape (`sk-…`, `ghp_…`, bearer tokens, JWTs,
  credentials embedded in a URL).
- **Input validation** at both edges: Pydantic for HTTP, JSON Schema for tools.
- **Permission checks** before every execution; approval boundaries the agent
  cannot talk its way past.
- **Safe error handling**: domain errors map to stable codes; anything else
  returns a generic 500 while the detail goes to the log only.
- **The calculator takes no shortcuts**: the expression is walked as an
  allow-listed AST, so calls, imports, attribute access and exponent blow-ups
  are rejected rather than sandboxed-by-hope.
- **The container runs as a non-root user.**
- Project `integrations` store only non-secret identifiers.

---

## Railway deployment

1. Create a Railway project and add a **PostgreSQL** database. Railway injects
   `DATABASE_URL`; the app normalizes it to the asyncpg driver automatically.
2. Deploy this repository. `railway.json` selects the Dockerfile build and sets
   the health check to `/api/health`.
3. Set the variables:

   | Variable | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | your key |
   | `ENVIRONMENT` | `production` |
   | `LOG_JSON` | `true` |
   | `CORS_ORIGINS` | your real origins, **not** `["*"]` |
   | `PERMISSION_EXECUTE` / `PERMISSION_DELETE` | keep `approval` |

4. Migrations run automatically on boot via `entrypoint.sh`
   (`alembic upgrade head`, then uvicorn). Set `RUN_MIGRATIONS=false` to run
   them as a separate release step instead.
5. `PORT` and `WEB_CONCURRENCY` are honoured.

---

## Extending the agent

**Add a tool** — subclass `Tool`, register it, done. The registry gives it
schema validation, permission enforcement, timeouts, crash isolation, redaction
and audit for free.

```python
from ulugbek_ai.core.enums import PermissionLevel
from ulugbek_ai.tools.base import Tool, ToolResult, ToolVerification

class GitHubCommitTool(Tool):
    name = "github_commit"
    description = "Commit and push changes to a repository branch."
    permission = PermissionLevel.EXECUTE          # → gated by approval
    input_schema = {
        "type": "object",
        "properties": {"repository": {"type": "string"},
                       "message": {"type": "string"}},
        "required": ["repository", "message"],
        "additionalProperties": False,
    }

    async def execute(self, arguments, context) -> ToolResult:
        sha = await push(arguments["repository"], arguments["message"])
        return ToolResult.success({"sha": sha}, sha=sha)

    async def verify(self, arguments, result, context) -> ToolVerification:
        # Prove it landed instead of assuming it did.
        if await commit_exists(arguments["repository"], result.evidence["sha"]):
            return ToolVerification.success("commit is present on the remote")
        return ToolVerification.failure("commit not found after push")
```

Then add it in `ulugbek_ai/tools/builtin/__init__.py::default_tools()`.

**Add a project integration** — put non-secret identifiers in
`Project.integrations` (`{"github": {"repository": "owner/repo"}}`). Credentials
stay in the environment.

**Add an LLM provider** — implement `LLMClient`. The agent depends on that
interface only.

**Add a retrieval strategy** — implement `MemorySearchStrategy`.

**Add a verification rule** — implement `VerificationCheck` and register it.

**Add authentication** — replace `require_principal()` in
`ulugbek_ai/api/deps.py`.

---

## License

Private project. All rights reserved.
