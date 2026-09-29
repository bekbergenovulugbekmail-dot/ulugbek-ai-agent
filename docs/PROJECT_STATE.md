# Project state

What is actually true about this system right now, and how each claim was
checked. Written to be re-verified rather than believed: every line below has a
command or a run behind it.

**Last verified:** 2026-09-29 · branch `claude/ulugbek-ai-agent-core-285chg`

---

## Live

| | URL | Verified by |
|---|---|---|
| API | <https://ulugbek-ai-agent-production.up.railway.app> | `/api/health` returns `status: ok` |
| Web Control Center | <https://frontend-production-b432.up.railway.app> | `/healthz` returns `status: ok` |

Both run on Railway as separate services from this one repository: the API
builds the root `Dockerfile`, the console builds `frontend/Dockerfile`.

Current production health:

```json
{"status":"ok","environment":"production",
 "database":{"connected":true,"error":null},
 "llm":{"configured":true,"model":"claude-opus-5",
        "workspace":{"configured":true,"usable":true,"problem":null}},
 "tools":{"count":15}}
```

## End to end, proven

A real agent request through the endpoint the console uses
(`POST /api/agent/runs`), carrying the console's own `Origin`:

```
run 1a7f813c-b79c-4baf-a5dd-d5da868c9c87
  status: RUNNING / task PENDING   (immediately after the POST)
  status: COMPLETED                (9 seconds later)
  output: pong
  error:  None
  iterations: 1   replans: 0
  runs carrying this request's marker: 1
```

The synchronous `POST /api/agent/run` was checked the same way and reported
`token_usage: 3954 in / 4 out` with `verification: SUCCESS` — real tokens, so a
real model answered.

Re-run it yourself: **Actions → Deployment check → Run workflow**, with
*"Also send one real agent request"* ticked. It spends tokens, so it is opt-in.

## Pipeline

`.github/workflows/ci.yml` on every push:

| Job | What it guards |
|---|---|
| Backend | migrations up→down→up on PostgreSQL 16, schema drift, 331 tests, pyflakes |
| Web Control Center | typecheck, lint, 52 tests, production build |
| Deploy to Railway | default branch only; waits for `/api/health` to answer `ok` |
| Deploy the Web Control Center | dormant until `RAILWAY_SERVICE_WEB` is set |

`.github/workflows/deployment-check.yml` is manual: it looks at production the
way a browser does — the API URL compiled into the deployed bundle, the CORS
headers the backend returns to the console's origin, and optionally one real
agent request.

## Configuration that matters

Two variables cause most outages here, and both fail in ways that do not look
like themselves:

| Variable | Where | Note |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Railway, web service | **Build-time.** Next compiles it into the browser bundle; setting it on a running container does nothing. `frontend/Dockerfile` refuses to build without it. |
| `ANTHROPIC_WORKSPACE_ID` | Railway, API service | Must be `wrkspc_…`. An organization or account id is rejected by Anthropic mid-run. Checked before the first request; `/api/health` reports whether it could be one, never its value. |

`CORS_ORIGINS` on the API must contain the web service's origin. It currently
returns `access-control-allow-origin: https://frontend-production-b432.up.railway.app`.

## Known gaps

- **No authentication.** `require_principal()` is a seam, not a check. Anyone
  who finds the API can run the agent.
- **The console's error hint is stale.** A configuration error shows the
  backend's precise message and then, under it, a fixed line recommending
  `ANTHROPIC_API_KEY` — which contradicts the message whenever something else
  is at fault.
- **No project-creation form.** Projects are created through the API.
- **SSE is cursor polling** at roughly 0.75s, not a push.
- **`RAILWAY_SERVICE_WEB` is unset**, so CI does not deploy the console; Railway's
  own GitHub integration does.
