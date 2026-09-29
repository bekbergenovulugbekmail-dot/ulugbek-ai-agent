# Next

Production works end to end and is closed to anonymous callers, verified
against the deployed service on 2026-09-29 (`docs/PROJECT_STATE.md` holds the
evidence). What follows is ordered by what would hurt most if it stayed as it
is, not by what is most interesting to build.

## 1. Watch production rather than visiting it

The deployment check is manual. On a schedule it would notice the next silent
outage — the backend was down for nine days before anyone looked — and the
`/api/health` body already carries everything such a check needs.

## 2. Creating a project needs the API

There is no form. Every project is created with a POST, which makes the
Projects page read-only in practice and the GitHub and Railway bindings
awkward to set up.

## Confirm, once

The `frontend` Railway service now has an **empty Root Directory** — that is
what the CI deploy needs, and clearing it is what made the first successful web
deploy possible. It also means that service's **GitHub integration must stay
off**. If it is still on, the next commit makes Railway build the *repository
root* for it: the root `railway.json`, the root `Dockerfile`, the backend
image, on the console's domain.

Check it once in the Railway dashboard. Nothing in this repository can see that
setting, and the first symptom would be the console answering as the API.

## Smaller

- SSE is a database cursor polled at ~0.75s; a real push would cut the latency
  and the query load together.
- `frontend/` is absent from `docker-compose.yml`, so the local stack is the
  API only.
- The agent has no shell tool, which it says plainly when asked to run a
  command. Adding one means deciding its permission level first — it would be
  the most dangerous tool in the registry.
