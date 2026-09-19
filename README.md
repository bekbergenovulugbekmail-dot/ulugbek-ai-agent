# ULUGBEK AI

A universal, modular, extensible **AI agent core** — not a chatbot.

The agent understands a request, loads only the context that matters, plans,
picks and runs tools under a permission policy, checks its own work, and stops
to ask a human before doing anything dangerous.

The repository holds two halves:

| | |
|---|---|
| **Agent engine** (`ulugbek_ai/`) | the brain — plans, uses tools, verifies, asks permission |
| **Web Control Center** (`frontend/`) | the console — watch it work and give it orders from a browser |

**GitHub is connected.** The agent can read a repository's state, commits, CI
runs and pull requests, and can open an issue — behind an approval. Railway,
Telegram, Instagram and ERP are later phases and plug in the same way: a `Tool`
subclass plus a `Project` integration binding, with no change to the engine, the
permission system, the API or the UI.

---

## Table of contents

- [The agent loop](#the-agent-loop)
- [Architecture](#architecture)
- [Web Control Center](#web-control-center)
- [Installation](#installation)
- [Environment variables](#environment-variables)
- [Local development](#local-development)
- [Database and migrations](#database-and-migrations)
- [Tests](#tests)
- [API](#api)
- [Memory](#memory)
- [Tools](#tools)
- [GitHub integration](#github-integration)
- [Railway integration](#railway-integration)
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
        ┌─────────────────────────┐
        │  frontend/  (Next.js)   │  browser console — talks HTTP only
        └────────────┬────────────┘
                     │ REST + Server-Sent Events
                 ┌───┴──────┐
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
├── events/          audit trail projected into a client-facing event stream
├── api/             routes, dependencies, error handlers
└── main.py          application factory

frontend/
├── src/app/         one route per page (App Router)
├── src/components/  layout · agent · cards · system · ui primitives
├── src/lib/api/     the central API client — components never call fetch
├── src/lib/hooks/   useResource (load/poll) · useRunStream (SSE) · useDebounced
└── tests/           vitest + Testing Library
```

**Technology:** Python 3.11 · FastAPI · SQLAlchemy 2.0 (async) · Alembic ·
PostgreSQL · Pydantic v2 · Anthropic SDK · Docker · Railway. Async throughout.

---

## Web Control Center

A dark, focused operations console — not an admin panel. Desktop is the primary
target; it works down to phone width.

```bash
cd frontend
npm install
cp .env.example .env.local        # NEXT_PUBLIC_API_BASE_URL -> your backend
npm run dev                       # http://localhost:3000

# Port 3000 already taken by something else? Pick another one:
npm run dev -- -p 3001            # or: $env:PORT=3001; npm run dev   (PowerShell)
```

**Stack:** Next.js 15 (App Router) · TypeScript (strict) · Tailwind CSS ·
Vitest + Testing Library. It is a pure client of the API: it imports no backend
code, holds no secrets, and needs only `NEXT_PUBLIC_API_BASE_URL`.

### Pages

| Page | What it is for |
|---|---|
| **Dashboard** | Greeting, counters, pending approvals, recent activity, system health, and a command box |
| **Agent** | The main screen: conversation on the left, live activity on the right |
| **Projects** | Every project; a detail page with tasks, memory, activity and integrations |
| **Tasks** | Filter by status; a detail page with the plan, execution timeline and tool runs |
| **Memory** | Browse by type, search by relevance (debounced) |
| **Approvals** | The approval centre — approve or reject a gated action |
| **Tools** | The registry with permission levels, plus execution history |
| **Activity** | One global timeline, filtered by kind and project |
| **Settings** | Connection, health and the permission model |

### Live activity

`POST /api/agent/runs` returns a `run_id` **immediately** and executes the run in
the background, so the console can follow the work instead of blocking on it.
`useRunStream` then subscribes to `GET /api/events/runs/{id}/stream`
(Server-Sent Events) and falls back to polling if the browser cannot hold the
stream open — both paths feed the same reducer, so the rest of the UI never
learns which is in use.

The status is always a concrete phase — *Loading context*, *Planning*, *Using a
tool*, *Verifying the result* — never a bare spinner. The model's private
reasoning is never shown: every line comes from the redacted audit trail.

### The event model

There is no second event table. The agent already writes a complete, redacted
trace to `agent_steps`, and `ulugbek_ai/events/` **projects** those rows into a
stable wire shape:

```jsonc
{
  "id": "…", "run_id": "…", "task_id": "…", "project_id": "…",
  "sequence": 7, "iteration": 2,
  "type": "tool.completed",        // agent.started · agent.planning · context.loaded
                                   // tool.started/completed/failed · approval.required
                                   // verification.started/completed · task.completed/failed
  "status": "success",             // info · running · success · failure · waiting
  "timestamp": "…",
  "safe_message": "Tool calculate: ok",
  "subject": "calculate",
  "metadata": { "ok": true, "duration_ms": 42 }   // summarised, never the whole payload
}
```

One source of truth, and `metadata` carries no secret because the trail was
redacted on write. Moving to WebSockets later changes `events.py` and
`useRunStream.ts` — nothing else.

### Component library

`AgentStatus` · `ActivityTimeline` · `ChatMessage` · `CommandInput` ·
`ProgressIndicator` · `TaskCard` · `ProjectCard` · `ApprovalCard` ·
`ToolExecutionCard` · `StatTile` · `HealthIndicator` · `Badge` · `Card` ·
`Button` · `EmptyState` · `ErrorState` · `LoadingState` / `Skeleton` ·
`ErrorBoundary` · `FilterTabs`.

Status colours live in one file (`src/lib/status.ts`), so a status can never
render green on one screen and amber on another.

### Frontend commands

```bash
npm run dev         # development server
npm run build       # production build
npm test            # vitest
npm run typecheck   # tsc --noEmit
npm run lint        # eslint
```

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
| `ANTHROPIC_API_KEY` | Claude API key. Without it the service still starts and serves every non-agent endpoint; `/api/agent/run` returns a clear configuration error. If the key spans several workspaces, also set `ANTHROPIC_WORKSPACE_ID` — otherwise every request is rejected with a 400. |
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
| `ANTHROPIC_WORKSPACE_ID` | *(empty)* | Only for a key spanning several workspaces — such a key runs in the workspace each request names. A single-workspace key needs nothing here. |
| `CLAUDE_MODEL` | `claude-opus-5` | Model id |
| `CLAUDE_MAX_TOKENS` | `16000` | Output cap |
| `CLAUDE_EFFORT` | `high` | `low` · `medium` · `high` · `xhigh` · `max` |
| `CLAUDE_THINKING` | `true` | Adaptive extended thinking |
| `CLAUDE_TIMEOUT_SECONDS` | `120` | Per-request timeout |
| `CLAUDE_MAX_RETRIES` | `2` | SDK-level retries |

### GitHub

| Variable | Default | Description |
|---|---|---|
| `GITHUB_TOKEN` | *(empty)* | Fine-grained PAT. Empty means public repositories only, at 60 requests/hour. |
| `GITHUB_API_URL` | `https://api.github.com` | Override for GitHub Enterprise |
| `GITHUB_TIMEOUT_SECONDS` | `20` | Per-request timeout |

Give the token the least it needs: *Metadata: Read*, *Contents: Read*,
*Actions: Read*, *Pull requests: Read* — and *Issues: Read and write* only if
you want `github_create_issue`.

### Agent loop

| Variable | Default | Description |
|---|---|---|
| `AGENT_MAX_ITERATIONS` | `12` | Hard cap on loop iterations |
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

### On Windows: one command

`start.ps1` brings the whole stack up in the right order and stops at the first
thing that is actually wrong, with the command that fixes it:

```powershell
.\start.ps1
```

It starts Docker Desktop if it is not running, waits for PostgreSQL to report
healthy, applies migrations, launches the API and the Web Control Center in
their own titled windows, waits until both answer a real request, and opens the
browser. Anything already running is left alone, so re-running it after a crash
restarts only the part that died. It reads no secret and prints none — only
whether a key is configured.

```powershell
.\start.ps1 -FrontendPort 3005   # port 3001 is taken by something else
.\start.ps1 -BackendPort 8010    # port 8000 is taken by something else
.\start.ps1 -SkipMigrations      # schema is known current
.\start.ps1 -NoBrowser           # do not open a browser
```

### With Docker (recommended)

```bash
cp .env.example .env      # set ANTHROPIC_API_KEY
docker compose up --build
```

Brings up PostgreSQL and the API, applies migrations, and serves
<http://localhost:8000/docs>.

Already running PostgreSQL on 5432? Set `POSTGRES_PORT=5433` in `.env` and use
the same port in `DATABASE_URL` — the container still listens on 5432 inside
the network, so nothing else changes.

### Without Docker

```bash
# a PostgreSQL instance must be reachable at DATABASE_URL
alembic upgrade head
python -m ulugbek_ai
```

### If the database will not connect

**Use `127.0.0.1`, not `localhost`, in `DATABASE_URL`.** On Windows `localhost`
resolves to `::1` (IPv6) first while Docker Desktop publishes the port on IPv4.
The TCP connection is accepted and immediately reset, and asyncpg reports it
during the SSL handshake — so it reads like a TLS problem rather than a wrong
address:

```
ConnectionResetError: [WinError 64] The specified network name is no longer available
  ... in _create_ssl_connection
```

`[WinError 1225] connection refused` is the other half of the same story: there
nothing is listening at all — the container is not up, or `DATABASE_URL` and
`POSTGRES_PORT` disagree.

Check the container is actually ready before migrating; `docker compose ps`
should say `healthy`, not `starting`:

```bash
docker compose ps
docker compose exec -T postgres pg_isready -U ulugbek -d ulugbek_ai
```

### Writing `.env` on Windows

PowerShell's `>` and `Out-File` write **UTF-16**, which the settings loader
cannot read. Use `Set-Content -Encoding Ascii` (or edit the file in an editor).
A UTF-8 BOM and CRLF line endings are both fine.

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

Migrations: `0001_initial` (the eight tables), `0002_tool_service` (records
which external service a tool execution spoke to).

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
resume) · the event projection and live visibility · the background runner ·
the GitHub client (projection, every error path, the token never leaking) and
its tools (repository resolution, approval gating, verify-by-read-back) ·
the HTTP surface.

The frontend suite (`cd frontend && npm test`) covers the API client (error
translation, query building, timeouts), the agent console (sending, streaming,
approval pause, failure), the approval flow, the command input, the activity
timeline, the project and task lists, and every loading / empty / error state.

---

## API

Interactive documentation: `/docs`. All routes are under `API_PREFIX`
(default `/api`).

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Liveness, database probe, tool count |
| `GET` | `/api/health/tools` | Every registered tool with its permission level |
| `POST` | `/api/agent/run` | Run the agent and wait for the result |
| `POST` | `/api/agent/runs` | Start a run in the background, return the id at once |
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
| `GET` | `/api/events` | Global activity feed |
| `GET` | `/api/events/runs/{id}` | Ordered events of one run |
| `GET` | `/api/events/runs/{id}/stream` | **Live event stream (SSE)** |
| `GET` | `/api/events/state` | The agent's current phase |
| `GET` | `/api/tools` | The tool registry |
| `GET` | `/api/tools/executions` | Tool execution history |
| `GET` | `/api/system/overview` | Dashboard counters, health and agent state |
| `GET` | `/api/projects/{id}/overview` | A project with its tasks, memory and activity |

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

And the GitHub integration:

| Tool | Permission | Description |
|---|---|---|
| `github_repo_info` | READ | Default branch, visibility, language, last push |
| `github_list_commits` | READ | Recent commits on a branch |
| `github_workflow_runs` | READ | GitHub Actions runs, with their conclusion |
| `github_list_pull_requests` | READ | Open / closed pull requests |
| `github_create_issue` | **EXECUTE** | Open an issue — requires approval, and verifies by reading the issue back |

And the Railway integration:

| Tool | Permission | Description |
|---|---|---|
| `railway_list_projects` | READ | Projects this token can see, with their ids |
| `railway_project_info` | READ | A project's services and environments, with ids |
| `railway_deployments` | READ | Recent deployments and their real status |
| `railway_deployment_logs` | READ | Log tail of one deployment, secrets masked |
| `railway_deploy` | **CRITICAL** | Deploy a service — always requires approval, waits for the outcome, and verifies against Railway's own status |

---

## GitHub integration

```
ulugbek_ai/integrations/github/
├── client.py   the API client — the only place the token exists
└── tools.py    five tools built on it
```

Two rules the client keeps:

- **The token never leaves `client.py`.** It is a `SecretStr`, sent only as a
  header. No error message or log line built here can contain it — there is a
  test that feeds the token back in a GitHub error body and asserts it does not
  surface.
- **Responses are projected, not forwarded.** A GitHub payload is tens of
  kilobytes; the agent needs a handful of fields. Every method returns a small
  dict, so a tool result cannot flood the model's context.

Failures come back as sentences a person can act on — a rejected credential, a
rate limit with what to do about it, a 404 that admits it might be a permissions
problem — never a bare status code.

### The repository comes from the project

You rarely name a repository. Bind it once:

```json
{ "github": { "repository": "ulugbek/telegram-bot" } }
```

and ask *"check my Telegram project"*. Every GitHub tool resolves the
repository the same way: an explicit argument wins, otherwise the current
project's binding, otherwise it asks rather than guessing. The binding is put in
front of the model through an **allow-list** of safe fields, so operational
detail in a binding never leaks into a prompt.

### Writes stop and ask

`github_create_issue` is classified `EXECUTE`, not `WRITE`: anything that
changes a service other people can see should pause for a human, whatever the
policy says about local writes. And it does not trust the API's response — its
`verify()` reads the issue back, so "created" means it is actually there.

### Checking it works

```bash
python -m scripts.smoke_github owner/repo      # real API, read-only
python -m scripts.smoke_claude "Check my projects"   # real model, one full run
```

`smoke_github` only runs the READ tools, so it can never change a repository.

---

## Railway integration

```
ulugbek_ai/integrations/railway/
├── client.py   the GraphQL client — the only place the token exists
└── tools.py    four read tools and one that deploys
```

Railway's API is GraphQL, which changes one thing that matters: **errors come
back with HTTP 200**, inside an `errors` array. A client that checks only the
status code reports a refused deploy as a success. This one checks the body,
and there is a test that sends a GraphQL error with a 200 and asserts it is
still treated as a failure.

Account, workspace and OAuth tokens authenticate with `Authorization: Bearer`;
a **project token uses its own header** and must not be sent as a bearer, so
`RAILWAY_TOKEN_KIND` declares which kind you have. A rejected credential says
which kind to try instead.

### Deploying always asks

`railway_deploy` is `CRITICAL` — the one level [the permission
policy](#permissions) will not let a configuration make automatic. Setting
`PERMISSION_CRITICAL=auto` does not make deploys automatic; it is corrected to
`approval` on construction. The gate is enforced in the executor, before the
tool runs: there is a test that approves a deploy and asserts the deploy
mutation was never sent to Railway beforehand, and another that rejects one and
asserts the same.

The approval card names what is about to happen:

> Deploy the service `'api'` to the `'production'` environment of project
> `'ulugbek-ai'` on Railway. This replaces what is currently running there and
> is visible to real users as soon as the build finishes.

That sentence comes from the tool itself, through `Tool.summarize()` — a hook
any tool can implement. Without it the card would show the word CRITICAL
against an empty argument list, because a deploy's target usually comes from
configuration rather than from the model.

### Triggering is not deploying

The deploy mutation returns as soon as Railway accepts the request, long before
anything is live. So `railway_deploy` waits for a terminal status and reports
what it actually saw:

| What Railway says | What the tool reports |
|---|---|
| `SUCCESS` | `live: true`, with the URL |
| `FAILED` / `CRASHED` | `live: false`, and to read the deployment logs |
| still building at the deadline | `live: false`, "still building — not confirmed live", and where to check |
| a status this client does not know | `unknown` — reported verbatim, never as success |

`verify()` then asks Railway again, independently of the trigger. It returns
success only when the deployment is live, failure when Railway says it failed
or when the trigger produced no deployment at all, and neither while a build is
still running — claiming success there is the exact failure this system exists
to prevent.

### The target comes from the project

Addressing a service needs three ids. Bind them once:

```json
{ "railway": {
    "project_id": "...", "environment_id": "...", "service_id": "..." } }
```

or set `RAILWAY_PROJECT_ID` / `RAILWAY_ENVIRONMENT_ID` / `RAILWAY_SERVICE_ID`.
An explicit argument beats the binding, which beats the defaults. When one is
missing the error names *which* one and which tool finds it, rather than
saying "invalid input".

### Logs are redacted at the source

Build output echoes environment variables as a matter of course, which makes
deployment logs the largest leak surface in the application. Every log line is
redacted and truncated inside `client.py`, before the text exists anywhere the
rest of the system can see it.

### Checking it works

```bash
python -m scripts.smoke_railway            # real API, read-only
```

`smoke_railway` never deploys: it runs the READ tools only.
`smoke_claude` needs `ANTHROPIC_API_KEY` and costs a few cents.

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
and audit for free. `ulugbek_ai/integrations/github/` is the worked example to
copy for Railway, Telegram, Instagram or ERP.

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
