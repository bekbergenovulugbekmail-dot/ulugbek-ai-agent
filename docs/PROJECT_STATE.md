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

Current production health, as the deployed build reports it (this branch adds
an `auth` block to it, and is not deployed):

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

## Authentication

**Live, and currently refusing everything.** This branch is the repository's
default branch, so pushing it deployed it; `AUTH_TOKEN` is not set on the
Railway API service, so the API answers 503 to every route but health. Setting
that one variable is the whole remedy — see *Known gaps*.

The model is one shared operator token, chosen because this deployment has one
operator: a user table, sessions and password reset would be machinery around a
single row.

| | |
|---|---|
| Credential | `AUTH_TOKEN`, at least 32 characters, compared with `hmac.compare_digest` |
| Applied | once, where the routers are assembled — not route by route |
| Open by design | `/api/health`, `/api/health/tools` |
| Missing / wrong token | `401`, identical body either way, `WWW-Authenticate: Bearer` |
| No token configured | `503` naming `AUTH_TOKEN`; health still answers |
| The live stream | `EventSource` sends no headers, so the console exchanges its token for a short-lived HMAC-signed one in the query string (`AUTH_STREAM_TOKEN_TTL_SECONDS`, default 300) |
| The console | one door before the whole app; the token lives in `localStorage` and is dropped the moment a request comes back 401 |

How it was checked, rather than assumed:

- **Every route, called for real.** `tests/test_auth.py` walks the application's
  routers — including the ones FastAPI keeps nested rather than flattened — and
  sends each an anonymous request, asserting 401 unless the path is on the
  open list, with a floor on how many it checked so a walk that finds nothing
  fails instead of passing. A first attempt at this test saw one route and
  passed; a deliberate negative control caught it.
- **The guard was removed on purpose** from `system.router` to confirm the
  sweep fails: `GET /api/system/overview answered 200 without a credential`.
- **The Anthropic key is not a user credential.** Sending it as a bearer is
  rejected like any other wrong token.
- **The token reaches no log**, no error body and no health response.
- 375 backend tests, 60 frontend tests.

One thing this cannot show: with a single operator there is no second identity
to be refused, so there is no meaningful cross-user authorization test. What is
tested is that a request cannot name its own owner — `/agent/run` and
`/agent/runs` discard `user_id` from the body, and an approval records the
authenticated caller rather than whoever the body claimed.

## Pipeline

`.github/workflows/ci.yml` on every push:

| Job | What it guards |
|---|---|
| Backend | migrations up→down→up on PostgreSQL 16, schema drift, 375 tests, pyflakes |
| Web Control Center | typecheck, lint, 60 tests, production build |
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
| `AUTH_TOKEN` | Railway, API service | Without it every route but health answers 503. The service still starts and still reports healthy, so a console pointed at it looks broken for no visible reason. Set it before deploying this branch. |
| `NEXT_PUBLIC_API_BASE_URL` | Railway, web service | **Build-time.** Next compiles it into the browser bundle; setting it on a running container does nothing. `frontend/Dockerfile` refuses to build without it. |
| `ANTHROPIC_WORKSPACE_ID` | Railway, API service | Must be `wrkspc_…`. An organization or account id is rejected by Anthropic mid-run. Checked before the first request; `/api/health` reports whether it could be one, never its value. |

`CORS_ORIGINS` on the API must contain the web service's origin. It currently
returns `access-control-allow-origin: https://frontend-production-b432.up.railway.app`.

## Known gaps

- **Production is deployed without its token.** Confirmed from a GitHub
  runner on 2026-09-29: `/api/system/overview` without a header answers
  **HTTP 503**, and `/api/health` reports `auth: {configured: false,
  usable: false}`. The service is up and the console can reach it; it is
  refusing everything because it has no credential to check against. Set
  `AUTH_TOKEN` on the Railway **API** service and the API serves again — no
  redeploy needed, since the variable is read at request time.

  The order was supposed to be the other way round. The deploy job is gated on
  the default branch, and the default branch here *is* the working branch, so
  the push shipped it. The pipeline now fails the deploy when the deployed
  service reports no usable token, instead of reporting green while production
  refuses every request.
- **The console's error hint is stale.** A configuration error shows the
  backend's precise message and then, under it, a fixed line recommending
  `ANTHROPIC_API_KEY` — which contradicts the message whenever something else
  is at fault.
- **No project-creation form.** Projects are created through the API.
- **SSE is cursor polling** at roughly 0.75s, not a push.
- **`RAILWAY_SERVICE_WEB` is unset**, so CI does not deploy the console; Railway's
  own GitHub integration does.
