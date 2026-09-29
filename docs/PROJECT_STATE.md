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

Current production health, as the deployed build reports it (deployment check
run 36542604824, 2026-09-29 08:25 UTC):

```json
{"status":"ok","version":"0.1.0","environment":"production",
 "database":{"connected":true,"error":null},
 "llm":{"configured":true,"model":"claude-opus-5",
        "workspace":{"configured":true,"usable":true,"problem":null}},
 "auth":{"configured":true,"usable":true},
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

**Live and enforced in production.** Verified against the deployed service from
a GitHub runner, not from a test suite — see *Measured against production*.

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

### What cannot be tested here, and why

**There is no cross-user 403 test, and there cannot be one.** Authorization
needs two identities: one that owns a thing and one that is refused it. This
deployment authenticates a single operator against a shared token, so every
authenticated request is the same principal — a "user B is denied user A's
project" test would have to invent user B, and would then be testing a fixture
rather than the system. Writing one would produce a green check that proves
nothing, which is worse than the gap it papers over.

What *is* tested, and is the meaningful half at one-operator scale, is that a
request cannot choose whose authority it acts with: `/agent/run` and
`/agent/runs` discard `user_id` from the body, and an approval records the
authenticated caller rather than whoever the body claimed (`decided_by` was
removed from the request model entirely).

This becomes testable the moment a second principal exists. The place to add it
is `_owned_by()` in `ulugbek_ai/api/routes/agent.py`, which is where a
principal would map to a user id; until then, treat the absence of that test as
a property of the deployment, not an oversight.

### Measured against production

Deployment check run 36542604824, 2026-09-29 08:25 UTC, against
`https://ulugbek-ai-agent-production.up.railway.app/api`. No token appears in
that log: the checks report status codes, field names and frame types, and
GitHub masks the header values.

| What was sent | Answer |
|---|---|
| `GET /health` (open) | `200`, `auth: {configured: true, usable: true}` |
| `GET /system/overview`, no header | **401** |
| `GET /system/overview`, wrong token of a plausible shape | **401** |
| `GET /system/overview`, real token without the `Bearer` scheme | **401** |
| the three refusal bodies | byte-identical; `www-authenticate: Bearer` present |
| `GET /system/overview`, real token, correctly presented | `200` — `agent, components, counters, environment, healthy, tasks_by_status, version` |
| `GET /events/runs/{id}/stream`, no token | **401** |
| `GET /events/runs/{id}/stream?token=<forged>` | **401** |
| `POST /events/stream-token` with the operator token | `200` — `token`, `expires_in: 300` |

The refusal body, identical for all three wrong ways of asking:

```json
{"error":{"code":"authentication_required","message":"This endpoint requires the operator token. Send it in the Authorization header.","details":{}}}
```

Then the same run the console makes, authenticated:

```
POST /agent/runs  -> 202   run 0eece5a2-be80-4189-b096-3d2e7dabcbb6
  status: RUNNING / task PENDING  (immediately)
  status: COMPLETED               (~9s later)   output: pong
  runs carrying this request's marker: 1

GET /events/runs/0eece5a2.../stream?token=<minted>
  -> 200 text/event-stream; charset=utf-8
  7 agent-event · 2 agent-state · 1 done   (10 frames)

POST /agent/run   -> 200   status: COMPLETED   output: pong
  verification: SUCCESS
```

Both agent paths were exercised on purpose: `/agent/runs` hands the run to a
background task on its own session, `/agent/run` holds the request open, and a
working one says nothing about the other.

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

- **One operator, one shared token.** Not a gap to close, but a limit to
  state: there is no second account, no per-user scoping, and therefore no
  cross-user authorization to test (see above). Rotating the token is the whole
  revocation story, and it logs out every browser at once.
- **Deploying briefly outran the variable.** On 2026-09-29 the auth branch
  shipped before `AUTH_TOKEN` existed on the service — the deploy job is gated
  on the default branch, and the default branch here *is* the working branch.
  Production answered 503 to everything but health for about twenty minutes,
  with a green pipeline behind it, because the pipeline only polls
  `/api/health` and that endpoint is open by design. It now reads the `auth`
  block in the response it was already fetching and fails the deploy when the
  deployed service reports no usable token.
- **The console's error hint is stale.** A configuration error shows the
  backend's precise message and then, under it, a fixed line recommending
  `ANTHROPIC_API_KEY` — which contradicts the message whenever something else
  is at fault.
- **No project-creation form.** Projects are created through the API.
- **SSE is cursor polling** at roughly 0.75s, not a push.
- **`RAILWAY_SERVICE_WEB` is unset**, so CI does not deploy the console; Railway's
  own GitHub integration does.
