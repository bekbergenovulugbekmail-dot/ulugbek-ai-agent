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

## Decide what speech should cost

The Uzbek model works and is too slow to talk to. Measured on 4 vCPU: **~21
seconds per transcription**, and the same 21 seconds whether the recording is
five seconds or thirty, because Whisper's encoder always runs over a 30-second
window. With the agent's own ~9 s that is half a minute per spoken command.

This is the one decision nobody else can make, because each way out costs
something different:

| | Effect | Cost |
|---|---|---|
| `RUBAI_AUDIO_CTX=768` + `STT_MAX_SECONDS=15` | **Measured: half the time**, transcripts identical for audio that fits | A spoken command may be at most 15 seconds |
| VAD (`--vad`) | Drops silence before the encoder sees it; would likely also fix `musiqa` | A second model and checksum. Not implemented, not measured |
| More vCPU on that service | Scales to roughly 8 threads | Money, monthly, continuously |
| A Whisper **small** fine-tune | ~3× faster, ~250 MiB | Worse on Uzbek, which is why it was fine-tuned |
| Back to a cloud API | Fast, nothing always-on | A per-minute bill, and the audio leaves the deployment |

The first row is measured, not estimated: −48.5% on 11 s of speech and −50.9%
on a 5 s clip, with byte-identical output. It is not the default because 768
positions hear only 15.4 seconds and the recording limit is 60 — the service
now refuses to start with that combination rather than silently dropping the
end of a sentence. Setting both together is the decision: **is a spoken command
ever longer than fifteen seconds?**

## Deploy the speech service

The image builds, runs and transcribes in CI; nothing of it is on Railway yet.

1. Railway → **New service** → this repository → root directory
   `services/rubai-stt`. Leave it **without a public domain**: the API reaches
   it privately and the model should not be on the internet.
2. On it: `STT_SERVICE_TOKEN` (generate with
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`). It refuses
   to start without one.
3. On the **API** service: `STT_PROVIDER=rubai`,
   `STT_SERVICE_URL=http://rubai-stt.railway.internal:8080`, and the same
   `STT_SERVICE_TOKEN`.
4. Then add `RAILWAY_SERVICE_WEB`'s equivalent for this service if you want CI
   to deploy it, the same way the console is deployed.

`/api/health` will then report `stt: {provider: "rubai", configured: true,
usable: true}` and the microphone appears in the console.

## Measure it on your own voice

Nobody has transcribed real Uzbek through this yet — the CI check transcribes
English, which proves the pipeline and nothing about accuracy. The model
author reports ~17% WER on their test set; that is a fact about their
recordings.

`docs/RUBAI_STT_BENCHMARK.md` has the method and says plainly that it holds no
results. Record twenty commands the way you would actually speak them, write
the manifest, and run `scripts/rubai_benchmark.py`. Until that is done, the
provider choice is not settled — `SpeechToText` exists so switching is one
adapter.

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
