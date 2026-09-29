# Next

Production works end to end and is closed to anonymous callers, verified
against the deployed service on 2026-09-29 (`docs/PROJECT_STATE.md` holds the
evidence). What follows is ordered by what would hurt most if it stayed as it
is, not by what is most interesting to build.

## 1. Let the pipeline deploy the console

`RAILWAY_SERVICE_WEB` is unset, so `deploy-web` skips and Railway's own GitHub
integration deploys the console instead. Both work, but the CI path is the one
that waits for `/healthz` before calling a deploy done. Setting the variable —
and `RAILWAY_WEB_HEALTHCHECK_URL` — closes that gap, provided the Railway
service is not also auto-deploying, or every push deploys twice.

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
