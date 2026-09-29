# Next

Production works end to end. What follows is ordered by what would hurt most
if it stayed as it is, not by what is most interesting to build.

## 1. Set `AUTH_TOKEN` in Railway, then ship the authentication branch

The code is written and tested; production is still open until it is deployed,
and deploying it in the wrong order breaks the service instead of protecting
it. The order matters:

1. Generate a token — `python -c "import secrets; print(secrets.token_urlsafe(48))"`
   — and set `AUTH_TOKEN` on the Railway **API** service. Nothing else changes:
   the console asks the operator for it in the browser.
2. Only then merge this branch to the default branch, which deploys it.
   Deployed without the variable, every route but `/api/health` answers 503 —
   the service stays healthy while the console shows nothing.
3. Check it from outside afterwards: `/api/health` still answers without a
   credential and reports `auth.usable: true`; a protected route answers 401
   without the header and 200 with it; the console asks for the token once and
   then runs the agent.

Rotating the token later invalidates every stored copy at once, which is the
whole recovery procedure.

## 2. The console's error hint contradicts its own message

`ErrorState` shows the backend's precise message and then a fixed line telling
the operator to set `ANTHROPIC_API_KEY`. When the key is fine and something
else is wrong — which is what happened with the workspace id — the two lines
disagree and the fixed one wins the reader's attention. Either drop the hint
where the message is already specific, or derive it from the error's code.

One-line change; left undone only because the instruction at the time was not
to touch application code.

## 3. Let the pipeline deploy the console

`RAILWAY_SERVICE_WEB` is unset, so `deploy-web` skips and Railway's own GitHub
integration deploys the console instead. Both work, but the CI path is the one
that waits for `/healthz` before calling a deploy done. Setting the variable —
and `RAILWAY_WEB_HEALTHCHECK_URL` — closes that gap, provided the Railway
service is not also auto-deploying, or every push deploys twice.

## 4. Watch production rather than visiting it

The deployment check is manual. On a schedule it would notice the next silent
outage — the backend was down for nine days before anyone looked — and the
`/api/health` body already carries everything such a check needs.

## 5. Creating a project needs the API

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
