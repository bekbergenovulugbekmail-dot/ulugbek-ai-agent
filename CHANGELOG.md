# Changelog

Notable changes, newest first. Entries say what changed and why it mattered,
because a list of verbs is not worth reading later.

## Unreleased

### Production brought up on Railway

The API and the Web Control Center now run as two Railway services built from
this repository, deployed by the pipeline when the default branch is green.

- **Deploy the Web Control Center as its own Railway service.** `frontend/`
  gained a Dockerfile shipping Next's standalone output, a `railway.json`, a
  `/healthz` route separate from the dashboard, and a deploy job of its own.
  The Dockerfile refuses to build without `NEXT_PUBLIC_API_BASE_URL`, because
  Next compiles that value into the browser bundle: supplied late, it produces
  a console that silently calls `localhost` from every visitor's browser.
- **Deploy the default branch automatically when it is green.** Migrations run
  before the tests on an untouched database, the tests run against PostgreSQL
  rather than SQLite, and the run only goes green once the deployment answers
  `/api/health` — a finished build is not a running service.

### Failures that used to be invisible

Four outages during bring-up shared a shape: the thing that broke could not be
seen from outside, so the first symptom was something unrelated.

- **Survive a database that is not ready yet.** A single `alembic upgrade head`
  under `set -eu` turned an ordinary startup race into a dead container. The
  server never started, so `/api/health` — built to report
  `database.connected: false` — never answered, and the platform served an
  opaque 502. Migrations now retry with a bounded backoff; when they still
  fail the container refuses to start but prints what to check, in order.
- **Catch a bad workspace id before Anthropic does.** `ANTHROPIC_WORKSPACE_ID`
  was the one identifier left out of the normalizer that strips blanks and
  quotes from credentials, and nothing checked its shape, so a wrong value
  surfaced as a 400 from Anthropic in the middle of a run naming a header the
  operator never set. It is now normalized, checked against the documented
  `wrkspc_` form before the client exists, and reported by `/api/health` as
  present/usable — never as a value.
- **Come up far enough to say what is missing.** A blank `ANTHROPIC_API_KEY`
  aborted startup, though the code intended a missing key to be non-fatal. A
  blank variable is the normal shape of a half-finished deployment, not an
  edge case. Blank now reads as unset for every credential, and the API
  resolves settings from the object it was built with rather than a global,
  so health cannot report a configuration the application is not running.
- **Stop attributing a finished run's outcome to the next one.** The console
  announced "the run finished without producing an answer" one second into a
  run that went on to complete, because the live-stream hook cleared its state
  in an effect rather than during render.

### Verification

- **A deployment check that sees what the browser sees.** Reads the API URL
  compiled into the deployed bundle, the CORS headers the backend returns to
  the console's origin, and — opt-in — makes one real agent request through
  the endpoint the console uses, following it to a terminal status and
  counting the runs it produced.
- **Railway integration** (five tools) with `railway_deploy` classified
  `CRITICAL`: the one permission level no configuration can make automatic.
  Triggering is not deploying, so the tool follows the deployment to a
  terminal status and withholds success while a build is still running.

## Phases

| Phase | What it added |
|---|---|
| 1 | The agent loop: plan, select tool, execute, observe, verify, replan — with permissions, approvals, memory and an audit trail |
| 2 | The Web Control Center: nine pages, live activity over SSE with a polling fallback |
| 3 | Real Claude and real GitHub, verified against the live APIs |
| 4 | Railway: deployment behind an approval gate, then production itself |
