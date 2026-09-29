# Next

Production works end to end and is closed to anonymous callers, verified
against the deployed service on 2026-09-29 (`docs/PROJECT_STATE.md` holds the
evidence). What follows is ordered by what would hurt most if it stayed as it
is, not by what is most interesting to build.

## 1. Two settings, and the pipeline deploys the console

Everything in the repository is done: the job is written, gated on the frontend
suite, waits for `/healthz`, queues rather than races, and — while it is still
dormant — prints the names of the services the Railway token can reach, so
there is nothing to guess. What is left is outside the repository:

1. **Settings → Secrets and variables → Actions → Variables**: add
   `RAILWAY_SERVICE_WEB` (the web service's name, as the skipped job prints it)
   and `RAILWAY_WEB_HEALTHCHECK_URL`
   (`https://<your-web>.up.railway.app/healthz`).
2. **Railway → the web service → Settings**: turn its GitHub integration off.
   Both deploy paths work alone; with both on, every push deploys twice and the
   two races decide which image ends up live.

Do them in that order and the next default-branch push deploys the console
through CI, which is the path that refuses to call a deploy done before the
service answers.

## 2. Watch production rather than visiting it

The deployment check is manual. On a schedule it would notice the next silent
outage — the backend was down for nine days before anyone looked — and the
`/api/health` body already carries everything such a check needs.

## 3. Creating a project needs the API

There is no form. Every project is created with a POST, which makes the
Projects page read-only in practice and the GitHub and Railway bindings
awkward to set up.

## Smaller

- SSE is a database cursor polled at ~0.75s; a real push would cut the latency
  and the query load together.
- `frontend/` is absent from `docker-compose.yml`, so the local stack is the
  API only.
- The agent has no shell tool, which it says plainly when asked to run a
  command. Adding one means deciding its permission level first — it would be
  the most dangerous tool in the registry.
