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
- 375 backend tests, 67 frontend tests.

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

## Voice

Implemented and tested. **Not live**: `STT_API_KEY` is not set on the Railway
API service, so the console shows no microphone in production yet.

```
🎤 MediaRecorder (audio/webm;codecs=opus)
 → POST /api/voice/transcribe   raw body · operator token · X-Audio-Duration-Seconds
 → Google Speech-to-Text        uz-UZ
 → transcript into the command box   ← the operator reads and edits it
 → Send → POST /api/agent/runs       ← unchanged
 → SSE                               ← unchanged
 → 🔊 the browser's own SpeechSynthesis
```

| | |
|---|---|
| Transcription | Google Cloud Speech-to-Text, `uz-UZ`, behind the `SpeechToText` interface |
| Synthesis | The browser's `SpeechSynthesis`. No server provider; `/api/voice/speak` says so |
| Containers accepted | `audio/webm`, `audio/ogg`, `audio/flac`. Safari's `audio/mp4` is refused **by name**, and the console hides the button rather than recording it |
| Limits | `STT_MAX_BYTES` (10 MB, enforced) and `STT_MAX_SECONDS` (60, respected by the recorder and checked against a declared duration) |
| Auth | The same operator token. Both routes joined the protected group in `api/router.py`; the existing anonymous sweep caught them without being told |
| Storage | None. Neither endpoint takes a session or the database, and a test asserts that rather than counting rows in the tables it thought to check |

What was verified, not assumed:

- **The transcript does not send itself.** A control that made it auto-send
  fails the test; so does one that offers to read a run still waiting for
  approval, and one that treats Safari's MP4 as recordable.
- **Removing the router guard** fails five tests, including the generic
  anonymous sweep — the new routes were covered the moment they were added.
- **Logging the provider exception's message instead of its type** fails the
  leak test: an HTTP library puts the full request URL, key and all, into both
  the message and the traceback, so the route logs the exception type alone.
- 395 backend tests, 83 frontend tests, typecheck, lint and a production build.

## Pipeline

`.github/workflows/ci.yml` on every push:

| Job | What it guards |
|---|---|
| Backend | migrations up→down→up on PostgreSQL 16, schema drift, 395 tests, pyflakes |
| Web Control Center | typecheck, lint, 83 tests, production build |
| Deploy to Railway | default branch only; waits for `/api/health` to answer `ok`, and fails if the deployed service has no usable token |
| Deploy the Web Control Center | default branch only; deploys `frontend` and waits for `/healthz`. If `RAILWAY_SERVICE_WEB` is ever unset it skips and prints the services the token can reach, rather than leaving the value to be guessed |

Deploys are not cancelled. The workflow still cancels a superseded run on a
working branch, but never on the branch that deploys: cancelling there kills
`railway up` or the health wait partway through and leaves a red X that means
"cancelled" rather than "broken" — a distinction already misread once here.
Each deploy job also holds its own concurrency group, so two runs never deploy
the same service at the same time; the later one waits rather than racing.

**Railway's own GitHub integration must be off for any service this pipeline
deploys.** Both paths work alone; with both on, every push deploys twice and
the two races decide which image ends up live.

### Both services now deploy from CI

Verified on 2026-09-29 across three runs.

| Run | What it showed |
|---|---|
| 36546729076 | Backend, console suite and the API deploy green; the web deploy skipped and printed the project's services — `Postgres`, `frontend`, `ulugbek-ai-agent` — which is where the value of `RAILWAY_SERVICE_WEB` came from |
| 36550451420 | With the variable set, the web deploy **failed**: `Root directory "/frontend" was not found in the deployed source` |
| 36551022651 | With the service's Root Directory cleared, the web deploy built `frontend/Dockerfile` and `/healthz` answered `{"status":"ok","service":"web"}` |

That failure settled a question the repository could not answer on its own:
**`railway up` applies the service's Root Directory to the uploaded source.**
The job uploads `frontend/`, so the `frontend` service's Root Directory must be
empty — the CLI has already narrowed the source to the right folder.

Which creates one coupling worth knowing: with an empty Root Directory, a
build triggered by Railway's own GitHub integration would use the *repository
root* — the root `railway.json`, the root `Dockerfile`, the backend image — on
the console's domain. So that integration is not merely redundant for this
service; it must stay off.

`.github/workflows/deployment-check.yml` is manual: it looks at production the
way a browser does — the API URL compiled into the deployed bundle, the CORS
headers the backend returns to the console's origin, and optionally one real
agent request.

## Configuration that matters

Two variables cause most outages here, and both fail in ways that do not look
like themselves:

| Variable | Where | Note |
|---|---|---|
| `STT_API_KEY` | Railway, API service | Optional. Without it the console shows no microphone and nothing else changes — the transcribe endpoint answers 503 and names the variable. A Google Cloud API key restricted to the Speech-to-Text API. |
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
- **No project-creation form.** Projects are created through the API.
- **SSE is cursor polling** at roughly 0.75s, not a push.
- **`RAILWAY_SERVICE_WEB` is unset**, so CI does not deploy the console; Railway's
  own GitHub integration does.
