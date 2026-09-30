# Next

Three services are live on Railway and the speech service joined them on
2026-09-29 (`docs/PROJECT_STATE.md` holds the evidence). Ordered by what would
hurt most if it stayed as it is, not by what is most interesting to build.

## 1. Nobody has spoken to it yet

Two separate gaps, and the second is the one that could invalidate a month of
work.

**Nothing has been transcribed in production.** `/api/health` reports
`stt: {provider: "rubai", configured: true, usable: true}`, which means the API
holds a provider, a URL and a token. It is not a probe. The two services have
never exchanged a request, and the first spoken command is what proves they
can. This costs one sentence into the microphone.

**No Uzbek has been measured anywhere.** The CI check transcribes English,
which proves the pipeline and says nothing about accuracy. The model author
reports ~17% WER on their own recordings; word error rate moves with the
microphone, the room, the dialect, and this console's vocabulary is unusual —
Uzbek sentences carrying `deploy`, `commit`, `Railway`, project names.

`docs/RUBAI_STT_BENCHMARK.md` has the method and says plainly that it holds no
results. `scripts/rubai_benchmark.py` is written and waiting. Record twenty
commands the way you would actually speak them, write the manifest, run it.

**Until that number exists the provider choice is not settled**, and neither is
any of the latency work: `SpeechToText` exists precisely so that switching is
one adapter.

## 2. The agent cannot see its own deployments

`RAILWAY_TOKEN` is not set on the API service, so every Railway tool fails.
Two variables, because the kind is not guessed:

```
RAILWAY_TOKEN=<a project token>
RAILWAY_TOKEN_KIND=project     # the default is `account`
```

Prefer a **project** token: it is scoped to one project and environment, where
an account token reaches everything. Getting `RAILWAY_TOKEN_KIND` wrong is a
confusing failure rather than an obvious one.

## 3. Watching production, one gap left

`.github/workflows/production-monitor.yml` now runs daily and stays quiet
unless something is wrong (`docs/PROJECT_STATE.md` → *Monitoring*). Eight
checkpoints, including a real transcription through the private network, which
is the only liveness signal the speech service can give.

What it still cannot tell you is **whether production is running the newest
commit**. `/health` reports `version: 0.1.0` — a constant — so a deploy that
silently failed to roll out looks exactly like one that worked. Closing that
means putting the build's commit SHA into the image and reporting it from
`/health`, which touches the Dockerfile and CI.

## 4. Confirm, once, in the dashboard

The `frontend` Railway service needs an **empty Root Directory** for the CI
deploy, which also means its **GitHub integration must stay off**. If it is
still on, the next commit makes Railway build the *repository root* for it: the
root `Dockerfile`, the backend image, on the console's domain.

Nothing in this repository can see that setting, and the first symptom would be
the console answering as the API.

The speech service is the opposite case and is correct as it is: its Root
Directory **is** `services/rubai-stt`, because the deploy job uploads the
repository root and lets Railway select the subdirectory. Setting both ends is
what made the first attempt fail.

## 5. Creating a project still has no form

`project_create` now exists as a tool, so the agent can register one when
asked. The Projects page is still read-only, which is fine while the console is
a conversation and worth revisiting if it stops being one.

## Speech, if it turns out to be too slow

Only worth opening after §1. The encoder window is done — sized per recording,
measured at 9,296 ms for eleven seconds of speech on four shared vCPU (run
36569415344), against ~21 s before. **Railway's own latency has not been
measured.** What is left costs money or accuracy:

| | Effect | Cost |
|---|---|---|
| More vCPU on that service | Scales to roughly 8 threads | Money, monthly, continuously |
| A Whisper **small** fine-tune | ~3× faster, ~250 MiB | Worse on Uzbek, which is why it was fine-tuned |
| VAD, with a tuned threshold | Fastest on silence; fixes the long-pause case | As shipped it **lost three sentences of five** at 55 s |
| Back to a cloud API | Fast, nothing always-on | A per-minute bill, and the audio leaves the deployment |

## Known and unfixed

A pause of about **fifteen seconds** mid-recording loses everything after it —
whisper's own segment handling, present before this work and unchanged by it.
VAD fixes that one case and loses more elsewhere. Silence transcribes as the
word `musiqa`.

## Smaller

- Railway warns that `railway.json` (Config as Code) is deprecated and that
  existing files keep working **until 2026-12-01**. Three services use one.
- SSE is a database cursor polled at ~0.75s; a real push would cut the latency
  and the query load together.
- `frontend/` is absent from `docker-compose.yml`, so the local stack is the
  API only.
- The agent has no shell tool, which it says plainly when asked to run a
  command. Adding one means deciding its permission level first — it would be
  the most dangerous tool in the registry.
