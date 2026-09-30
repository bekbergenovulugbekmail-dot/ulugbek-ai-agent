# Changelog

Notable changes, newest first. Entries say what changed and why it mattered,
because a list of verbs is not worth reading later.

## Unreleased

### Production is watched rather than visited

The backend was once down for nine days before anyone looked, and there are
three services now — one of which, the speech service, has no public domain and
so cannot be asked from outside Railway at all.

A daily workflow runs eight named checkpoints and **says nothing when they
pass**. The notification is the failed run itself: a green mail every morning
is a green mail nobody reads, and the first red one would be read as one more
of them.

Two things it gets right that a naive check would not. **HTTP 200 is not
health** — the API answers 200 with its database unreachable, and `stt.usable`
is a statement about configuration that stays true with the speech service
switched off, so each check reads the body. And **Railway answers 502 while it
replaces a container**, so every network check is retried before it is
believed; a monitor that pages on a cold start gets muted, and a muted monitor
is worse than none. The one check that is deliberately not retried is
`auth refuses anonymous`: an API that answered a caller with no credential has
answered.

The speech service is probed through `POST /api/voice/transcribe`, because the
API can reach it over the private network and nothing else can. A real
transcription is the only evidence the two services can still speak.

The logic is a tested Python script rather than bash inside YAML: 34 tests
through a transport double, and four negative controls — removing the retry,
trusting 200 as health, dropping the redaction, letting the anonymous probe
carry the token — each caught by exactly the test written for it.


### The speech timeouts are now in an order that works

`STT_TIMEOUT_SECONDS` and `STT_MAX_SECONDS` were both 60. A 55-second
recording — shorter than the limit actually accepts — was measured end to end
at 59,870 ms on four shared vCPU (run 36566943407), which left 130 milliseconds.
Past that the API answers 504 while the speech service keeps a CPU busy
finishing a transcript nobody is waiting for, and tells the operator to try
again, which starts a second one.

Three waits now nest: the API waits **150 s**, the speech service caps its
whole answer at **120 s**, and the audio it accepts at all stays at **60 s**.
The cap is enforced rather than defaulted, and the queue wait and the inference
share one deadline, so two requests behind one slot cannot add up to twice the
budget. A model that outruns the budget answers **504** instead of 502, and the
API turns that into a timeout rather than a bad gateway — "too slow" and
"broken" are different things to tell someone.

None of the three is measured on Railway hardware; they are sized from a
four-vCPU runner and deliberately loose.

### The encoder window now fits the recording

`whisper-server` reads `audio_ctx` from the request form, not only from the
command line. That removes the problem a fixed window created: the service
already measures each recording to enforce its limit, so it asks for a window
that fits it — no chunking, no overlap, no transcripts to merge, and so no
duplicated words at a seam and no order to get wrong.

Measured across nine samples and three configurations (run 36566943407):

- **A spoken command is a little more than twice as fast.** 11 seconds of
  speech went from 21,269 ms to 9,330 ms, the same audio as Opus from 21,323 to
  9,291 — the window was 651 positions instead of the full 1500.
- **It fixed a case the full window got wrong.** At 22 seconds the baseline
  returned one sentence out of two; sizing the window to the audio returned
  both.
- **Nothing was lost anywhere.** 11, 22, 33 and 55 seconds all returned every
  sentence, a 30-second silence returned nothing, and 66 seconds was refused
  with 413 as it should be.
- Past whisper's own 30-second chunk the window goes back to the full 1500, so
  those samples run the same code in both configurations — the 27% spread
  between them there is the runner, and is the noise floor for everything else.

**Voice activity detection is carried and switched off.** It is by far the
fastest thing on silence — 20,431 ms to 503 — and the only configuration that
gets a fifteen-second pause mid-sentence right. It also lost three sentences of
five on the 55-second sample. A transcriber that silently drops speech it
judged too quiet is not a latency improvement, so it needs a threshold someone
has measured before it goes anywhere near a default. The model is pinned like
the rest: `ggml-org/whisper-vad` @ `9ffd54a1`, 885,098 bytes, sha256
`29940d98…`, MIT.

Known and unfixed: a long silence in the middle of speech loses the second
half. That is whisper's own segment handling, it predates this work, and the
only thing that fixed it in these runs cost more than it saved.

### Speech runs on our own hardware, and is too slow to use as it stands

Transcription moved from Google to **rubaiSTT v2 medium** — a Whisper-medium
fine-tune for Uzbek Latin — on whisper.cpp in `services/rubai-stt`. No cloud
key, no per-minute bill, and the audio never leaves the deployment.

- **A service of its own**, not the API container: 514 MiB of weights in the
  process that serves the agent would trade a service that starts in seconds
  for one that starts in a minute and cannot be sized apart from the model.
  whisper.cpp listens on loopback inside that container; the only thing on a
  reachable port is the guard in front of it, which checks the token, enforces
  the limits, converts with ffmpeg and deletes every byte it wrote.
- **Pinned by content.** whisper.cpp v1.9.4 (MIT); the GGML conversion at
  commit `a8498d5`, 539,212,484 bytes, sha256 `3740210b…`, Apache-2.0, from
  `islomov/rubaistt_v2_medium`, also Apache-2.0. A provenance workflow produced
  those values where Hugging Face is reachable and the build re-checks all
  three, so different weights cannot arrive behind the same Dockerfile.
- **Safari works now.** ffmpeg decodes whatever the browser recorded, so
  `audio/mp4` joins the containers the console records and the backend accepts.

**Measured, twice, and the second run corrected the first.** The same image on
the same sample took 21,493 ms on one runner and 12,037 ms on another, so the
"~21 seconds" first reported is really "12 to 21 seconds on four shared vCPU".
Only comparisons made inside a single run are worth anything, which is what the
latency harness does.

**Shortening Whisper's encoder context halves the time** — −48.5% on 11 s of
speech, −50.9% on a 5 s clip, with byte-identical transcripts. It is still not
the default: 768 positions hear 15.4 seconds, `STT_MAX_SECONDS` accepts 60, and
the 22-second sample came back different. The service refuses to start when the
context cannot reach the end of a recording it would accept, so the 50% can be
taken deliberately — `RUBAI_AUDIO_CTX=768` with `STT_MAX_SECONDS=15` — and not
by accident. `docs/RUBAI_STT.md` has every number and VAD as the next
candidate, documented rather than implemented.

Two things the same run showed: the Uzbek fine-tune still transcribes English
correctly, and it hallucinates the word "musiqa" over silence.

### The operator can speak to the agent

Uzbek in, Uzbek out. The agent already answered in whatever language it was
asked in — `prompts.py` has said so since the first phase — so this adds no
language handling at all. It adds a microphone in front of the text path and a
voice behind it.

- **`POST /api/voice/transcribe`** takes the recording as a raw body, sends it
  to Google Speech-to-Text as `uz-UZ`, and returns the text. Raw rather than
  multipart, which would cost a dependency this service has no other use for,
  and rather than base64, which would add a third to the size of every
  recording on the operator's uplink.
- **The transcript goes to the operator, not to the agent.** It lands in the
  command box for review and waits for Send. Uzbek is low-resource and every
  transcriber mishears it sometimes; a run started from an unreviewed
  transcript is a run that acts on a sentence nobody said.
- **Answers are read aloud by the browser.** `SpeechSynthesis` needs no key,
  costs nothing and sends no audio anywhere, so `TTS_PROVIDER` defaults to
  `browser` and `/api/voice/speak` says plainly that this service produces no
  audio. The endpoint and the `TextToSpeech` interface exist so that adding a
  server voice later is an adapter and a branch — Azure has real `uz-UZ` neural
  voices — with no new route and no new path through the console.
- **The microphone is absent, not broken, where it cannot work.** Safari
  records MP4, which the provider cannot decode; the console checks
  `MediaRecorder.isTypeSupported` against the containers the backend accepts
  and shows no button rather than one that fails on every upload.

Nothing about the agent, the event stream, approvals or the database changed. A
speech outage costs the console its microphone and nothing else, and both voice
endpoints sit behind the same operator token as everything else — the route
sweep in `tests/test_auth.py` picked them up without being told they existed.

Two guarantees are tested rather than intended: the key appears in no log, no
error body and no health response, and neither endpoint can reach the database,
so audio cannot be stored by either of them however they are changed later.

### Deploys stop being cancellable, and the dormant one says what it needs

- **A new push no longer kills a running deploy.** The workflow cancelled any
  superseded run on any branch, including the branch that deploys — so a push
  landing during `railway up` or the health wait ended both partway through,
  and left a run marked with a red X that meant "cancelled", not "broken".
  That distinction was already misread once here. Working branches keep the
  fast cancel; the default branch runs to completion.
- **One deploy per service at a time.** Each deploy job holds its own
  concurrency group and queues rather than cancels, so two runs cannot race on
  the same service and land the older commit last. The API and the console have
  separate groups — they are separate services and need not wait for each other.
- **The dormant web deploy now says what to set it to.** It had skipped since
  it was written, telling the operator to set `RAILWAY_SERVICE_WEB` without
  saying what the value was. It now uses the token it already has to print the
  names of the services in the project — names are not secret, the token is
  never printed — so the variable can be copied rather than guessed.

Railway's own GitHub integration has to be off for any service this pipeline
deploys: with both on, every push deploys twice and the two races decide which
image ends up live. That is a dashboard setting, and the workflow header says
so where someone reading it will look.

**Both services now deploy from CI.** Turning the web deploy on produced one
failure worth recording: `Root directory "/frontend" was not found in the
deployed source`. `railway up` applies the service's Root Directory to the
uploaded source, and the job uploads `frontend/` — so that service's Root
Directory has to be empty, because the CLI has already narrowed the source.
With it cleared, the deploy built `frontend/Dockerfile` and `/healthz` answered
`{"status":"ok","service":"web"}`. The consequence is now in the docs: an empty
Root Directory means a GitHub-integration build of that service would use the
repository root and put the backend image on the console's domain.

### The console stops contradicting the backend

`ErrorState` showed the backend's precise configuration message and then, under
it, a fixed line telling the operator to set `ANTHROPIC_API_KEY` and restart.
When the cause was something else — the workspace id, which is exactly what
happened — the two lines disagreed and the fixed one won the reader's
attention, sending whoever was debugging it at the wrong variable.

The hint now says *where* a configuration error is fixed, never *which*
setting: every such error the backend raises already names its own variable and
what to do about it. The title became "The backend is not configured" for the
same reason — a configuration error can be the model credential, the operator
token or the runner, and only one of those is the agent. Network,
authentication and ordinary failures are untouched, and seven regression tests
hold the line: three of them fail on the old component.

### The API is no longer open

Until now `require_principal()` returned an operator to everyone who asked.
Anyone who found the URL could run the agent on the configured key, read every
project and memory, and decide an approval. It now takes a credential.

- **One shared operator token, applied once.** `AUTH_TOKEN` (at least 32
  characters, compared in constant time) is required by every route except
  `/api/health` and `/api/health/tools`. The guard is attached where the
  routers are assembled rather than listed on each route — when it was listed
  that way, twenty-one of thirty-three endpoints never mentioned it, the whole
  event feed and the dashboard's overview among them. Anything added there
  inherits it.
- **An unauthenticated caller learns nothing.** Router-level dependencies run
  before an endpoint's own, so a request stops at the guard instead of first
  resolving the LLM dependency and being told which credential the server is
  missing. A wrong token, a malformed header and no header at all answer
  identically; a server with no token configured answers 503 and names the
  variable, because refusing everyone is the only safe reading of that.
- **A credential for the live stream.** `EventSource` cannot send headers, so
  the console exchanges the operator token for a short-lived HMAC-signed one
  that rides in the query string. It expires on its own and is stored nowhere.
- **The caller can no longer name themselves.** `/agent/run` and `/agent/runs`
  discard any `user_id` in the body, and an approval records the authenticated
  principal — `decided_by` is gone from the request model. A record of who
  decided is worth keeping only if the decider did not choose the name.
- **One door in front of the console.** The operator's token is entered once,
  kept in `localStorage`, attached to every request, and dropped the moment the
  backend refuses it — so a rotated token shows one explanation instead of nine
  unrelated broken panels.

`/api/health` reports whether a usable token is configured, as two booleans.
The value appears in no log, no error body and no health response, and the
Anthropic key is rejected as a user credential: it authenticates this server to
Anthropic and nothing else.

**Verified against production**, not only in tests — deployment check run
36542604824 on 2026-09-29. An anonymous request, a wrong token of a plausible
shape and the real token sent without the `Bearer` scheme all answered 401 with
byte-identical bodies and a `WWW-Authenticate` challenge; the correct token
answered 200. The live stream refused both a missing and a forged token, and
opened with a minted one, delivering 10 frames for a real run. Both agent paths
completed on the deployed service: `/agent/runs` (202, then COMPLETED, exactly
one run for the request) and `/agent/run` (200, COMPLETED, verification
SUCCESS). No token appears in that log.

There is no cross-user 403 test and there cannot be one: authorization needs a
second identity to refuse, and this deployment has one operator. `docs/PROJECT_STATE.md` says so plainly rather than substituting a fixture that would
pass without proving anything.

**The deploy briefly outran the variable.** The branch shipped before
`AUTH_TOKEN` existed on the Railway service, because the deploy job is gated on
the default branch and the default branch here *is* the working branch.
Production answered 503 to everything but health for about twenty minutes,
behind a green pipeline — the pipeline polls `/api/health`, which is open by
design and answers `ok` regardless. It now reads the `auth` block in the
response it was already fetching and fails the deploy when the deployed service
reports no usable token, naming the variable.

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
