# The Uzbek speech service

Transcription runs on the project's own hardware allowance, not on a cloud API.
The model is **rubaiSTT v2 medium** — a Whisper-medium fine-tune for Uzbek in
the Latin script — executed by **whisper.cpp** in a service of its own.

---

## Where it sits

```
browser  🎤 MediaRecorder
   │  audio/webm;codecs=opus   (audio/mp4 on Safari)
   ▼
ulugbek-ai-agent           POST /api/voice/transcribe      operator token
   │                       size and duration refused here first
   ▼  Railway private network — no public domain
rubai-stt                  POST /inference                 service token
   │  ffmpeg → 16 kHz mono WAV → whisper.cpp (loopback, model held warm)
   ▼
transcript → the command box → the operator presses Send → the existing agent
```

Two containers because the model is 514 MiB. Loading it into the API process
would trade a service that starts in seconds for one that starts in a minute,
and would tie the agent's memory to the model's. Separated, either can be
restarted, resized or switched off without the other noticing.

The model server itself binds `127.0.0.1` **inside** its container. The only
thing listening on a port anyone can reach is the guard in front of it.

---

## What this cannot do

Two limits, stated here rather than left to be discovered from a transcript
that looks fine and is not.

**A pause of about fifteen seconds in the middle of a recording loses
everything after it.** Measured: a 37-second sample built as speech, fifteen
seconds of silence, speech returned only the first half — under the default
`auto` window and under the full window alike (run 36566943407). Three seconds
of pause is fine; the same sample with a three-second gap returned both halves.
This is whisper's own segment handling, it predates the window work and is
unchanged by it. Turning VAD on does fix this one case and loses more elsewhere
— three sentences out of five on a 55-second sample — so it stays off.

For an operator that means: **do not stop and think for a quarter of a minute
mid-recording.** Stop the recording, send it, and start another. There is no
warning when this happens — the request succeeds and the transcript simply ends
early — so the only defence is reading the transcript before pressing Send,
which the console already requires.

**Silence transcribes as the word `musiqa`.** A recording with no speech in it
does not come back empty. Unchanged by any of this work and not fixed here.

---

## Provenance

The weights are a binary from a community account, so they are pinned by
content. These values came from a machine that could reach Hugging Face —
`.github/workflows/rubai-model-provenance.yml`, run 36558235028 on 2026-09-29 —
and the image build verifies all three.

| | |
|---|---|
| Base model | [`islomov/rubaistt_v2_medium`](https://huggingface.co/islomov/rubaistt_v2_medium) — Whisper-medium fine-tune, 769M parameters, Uzbek Latin |
| Licence | **Apache-2.0** (declared in the model card front matter) |
| Author's own figures | ~17% WER / 5.5% CER on the author's test set, ~475 h of mixed Uzbek audio, 50% human-transcribed |
| GGML conversion | [`azimxxm/rubaistt-v2-medium-ggml`](https://huggingface.co/azimxxm/rubaistt-v2-medium-ggml) |
| Commit | `a8498d5fd8d0b35e26a6b74521cf194c37c6a050` |
| File | `ggml-rubaistt-medium-q5_0.bin` |
| Size | 539,212,484 bytes (514 MiB) |
| sha256 | `3740210b611c13c5bad257d6207dbf4572b7843043191bab39ccdf47c1734e9c` |
| Magic | `6c6d6767` — `ggml`, little-endian |
| Conversion licence | **Apache-2.0**, with `base_model: islomov/rubaistt_v2_medium` declared |
| Runtime | [whisper.cpp](https://github.com/ggml-org/whisper.cpp) **v1.9.4** — **MIT** |

Voice activity detection, carried in the image and switched off (run
36566313739 produced these):

| | |
|---|---|
| Repository | [`ggml-org/whisper-vad`](https://huggingface.co/ggml-org/whisper-vad) |
| Commit | `9ffd54a1e1ee413ddf265af9913beaf518d1639b` |
| File | `ggml-silero-v5.1.2.bin` |
| Size | 885,098 bytes (865 KiB) |
| sha256 | `29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf` |
| Licence | **MIT** |
| Upstream | Silero VAD, MIT |

The licence chain is Apache-2.0 → Apache-2.0 → MIT, with the conversion's card
naming its base model, so the whole stack is redistributable.

**A build cannot drift.** The Dockerfile fetches the file at that commit,
checks the sha256, and checks the first four bytes are the GGML magic — an HTML
error page saved under the right name passes every check but the last.

The conversion's author states that q5_0 produced identical text to q8_0 on
their Uzbek test sentences, which is why only q5_0 is published. That claim has
not been re-tested here.

---

## Resources — measured

Two runs of `.github/workflows/rubai-stt-latency.yml` on GitHub runners
(4 vCPU), same image, same samples.

### The absolute numbers move with the runner

The same image transcribing the same 11-second file:

| run | baseline latency |
|---|---|
| 36560660876 | **21,493 ms** |
| 36563879708 | **12,037 ms** |

Nothing changed between them but which machine picked up the job. **Any single
absolute latency from CI is worth ±80%**, so the earlier "~21 s" should be read
as "somewhere between 12 and 21 seconds on four shared vCPU" and a Railway
container will be its own number again. What *is* trustworthy is a comparison
made inside one run, on one machine, one container after another — which is
what the harness does and why it does it that way.

Container memory, model loaded and idle: **873 MiB**.

### Shortening the encoder window: ctx=0 against ctx=768

Run **36563879708**, fastest of two repeats each:

| sample | audio | ctx=0 | ctx=768 | change | rtf ctx=0 | rtf ctx=768 |
|---|---|---|---|---|---|---|
| `jfk.wav` (speech) | 11.0 s | 12,037 ms | 6,203 ms | **−48.5%** | 1.09 | 0.56 |
| `jfk.webm` (same, Opus — the browser path) | 11.0 s | 12,366 ms | 6,226 ms | **−49.7%** | 1.12 | 0.56 |
| `jfk-long.wav` (the same speech twice) | 22.0 s | 12,413 ms | 7,343 ms | **−40.8%** | 0.56 | 0.33 |
| silence | 5.0 s | 12,036 ms | 5,909 ms | **−50.9%** | 2.40 | 1.18 |
| silence | 30.0 s | 12,004 ms | 5,932 ms | **−50.6%** | 0.40 | 0.19 |

The realtime factor is seconds of compute per second of audio. Below 1.0 is
faster than the speech being transcribed; the 5-second row is the one that
matters for a spoken command, and 768 takes it from 2.40 to 1.18.

**Roughly half the time, on every sample.** The first comparison
(36562868898, on the slower runner) found −49.7%, −49.9%, −51.1% and −50.9% on
the four samples it had. The effect reproduces across runners even though the
absolute numbers do not.

### What it cost

| sample | transcript |
|---|---|
| `jfk.wav`, `jfk.webm` | **identical**, 108 → 108 characters |
| silence, 5 s and 30 s | **identical** — `musiqa` in both. The hallucination on silence is unchanged, neither better nor worse |
| `jfk-long.wav` | **changed**, 108 → 435 characters |

That last row is the finding. 768 encoder positions cover **15.4 seconds** —
Whisper's encoder has 1500 positions over a 30-second window, so the window
shrinks by exactly that ratio. The 11-second samples fit inside it and came
back untouched; the 22-second one did not, and the two configurations disagreed
about it. (The sample is the same sentence twice, so it is a poor measure of
*which* answer is better — the baseline collapsed the repetition, 768 emitted
it several times. It is a decisive measure of *that they differ*, which is the
question that matters here.)

### So: a fixed number is the wrong shape

A *pinned* 768 was never adopted. Audio past the shortened window is not
transcribed badly — it is not transcribed at all, and the request still
succeeds with a plausible-looking transcript that is missing its end. A single
number that is right for a five-second command is wrong for a twenty-second
one, and the recording limit is sixty.

The answer was to stop picking a number. `whisper-server` reads `audio_ctx`
from the request form as well as the command line
(`examples/server/server.cpp:510`), so one warm model can serve a window sized
to each recording: `ceil((duration + headroom) × 50)`, floored at 256, and 0
once that reaches whisper's full 1500. The window then always reaches the end
of the audio, which is the property a fixed number cannot have.

A pinned number is still available, and the service **refuses to start** when
one cannot reach the end of a recording it would accept — naming both
variables, because that combination loses words without saying so.

## Long speech: what the window does, and what VAD does

Run **36566943407**, one GitHub runner (4 vCPU), three configurations one after
the other on the same image and the same samples. Every speech sample is the
same eleven-second sentence repeated a known number of times, so "did anything
go missing" is a count of a distinctive phrase rather than a judgement: fewer
means speech was dropped, more means the decoder repeated itself.

### Transcript integrity — phrase occurrences, found/expected

| sample | audio | `full` (ctx 0) | `auto` | `auto+vad` |
|---|---|---|---|---|
| `jfk.wav` | 11 s | 1/1 | **1/1** | 1/1 |
| `jfk.webm` (Opus) | 11 s | 1/1 | **1/1** | 1/1 |
| `speech-22s` | 22 s | **1/2 ✗** | **2/2 ✓** | 1/2 ✗ |
| `speech-33s` | 33 s | 3/3 | **3/3** | 3/3 |
| `speech-55s` | 55 s | 5/5 | **5/5** | **2/5 ✗** |
| `gap3-25s` (speech · 3 s · speech) | 25 s | 2/2 | **2/2** | 2/2 |
| `gap15-37s` (speech · 15 s · speech) | 37 s | **1/2 ✗** | **1/2 ✗** | 2/2 ✓ |
| `silence-30s` | 30 s | 0/0 | **0/0** | 0/0 |
| `speech-66s` | 66 s | HTTP 413 | **HTTP 413** | HTTP 413 |

### Wall time, ms

| sample | `full` | `auto` | `auto+vad` | window `auto` used |
|---|---|---|---|---|
| `jfk.wav` 11 s | 21,269 | **9,330** (−56%) | 9,451 | 651 |
| `jfk.webm` 11 s | 21,323 | **9,291** (−56%) | 9,366 | 651 |
| `speech-22s` | 21,306 | **17,880** (−16%) | 17,179 | 1201 |
| `speech-33s` | 43,671 | 55,575 | 23,806 | 0 |
| `speech-55s` | 47,018 | 59,870 | 44,490 | 0 |
| `gap3-25s` | 22,371 | 20,428 | 20,661 | 1351 |
| `gap15-37s` | 42,846 | 43,188 | 22,912 | 0 |
| `silence-30s` | 20,431 | 20,408 | **503** | 0 |

Memory: 920 MiB `full`, 1.05 GiB `auto`, 899 MiB `auto+vad`.

**The 33 s and 55 s rows are noise, not signal.** Both configurations send
`audio_ctx=0` there — past whisper's own 30-second chunk the window goes back
to the full 1500 — so they run identical code and the 27% spread between them
is the runner. Read it as the noise floor for every other number on this page.

### What was decided

**`auto` is the default.** For audio inside one chunk it is a little more than
twice as fast on a spoken command, and it did not lose a word anywhere the
baseline did not lose one first. It also *fixed* a case the full window got
wrong: at 22 seconds the baseline returned one sentence out of two, and sizing
the window to the audio returned both.

**VAD stays off.** It is the fastest thing here on silence — 20,431 ms to 503,
because there is nothing to decode — and it is the only configuration that gets
the fifteen-second gap right. It also **lost three sentences out of five** on
the 55-second sample and one of two at 22 seconds. A transcriber that silently
drops speech it judged too quiet is not a latency improvement; it is the
failure this whole exercise exists to avoid. The model is carried in the image
and one variable away, for whoever wants to measure it against a different
threshold.

### Known, and not fixed here

**A long silence in the middle of speech loses the second half.** `gap15-37s`
returned one sentence of two under both `full` and `auto` — this is whisper's
own segment handling, present before any of this work and unchanged by it. VAD
fixes it and costs more than it saves. The next thing to try is
`--vad-min-silence-duration-ms` tuned up, or splitting on long silences in the
service; neither is done, and neither is measured.

---

## Configuration

On the **API** service:

```
STT_PROVIDER=rubai
STT_SERVICE_URL=http://rubai-stt.railway.internal:8080
STT_SERVICE_TOKEN=<the same value as on the speech service>
STT_LANGUAGE=uz-UZ
STT_MAX_BYTES=10485760
STT_MAX_SECONDS=60
STT_TIMEOUT_SECONDS=150
```

### The three waits

They only work in this order, each one longer than the one inside it:

| | | |
|---|---|---|
| The API waits for an answer | `STT_TIMEOUT_SECONDS` | **150 s** |
| The speech service's whole answer, queue wait included | `RUBAI_REQUEST_TIMEOUT_SECONDS`, capped | **120 s** |
| The audio it will accept at all | `STT_MAX_SECONDS` | **60 s** |

`STT_TIMEOUT_SECONDS` was 60, and that was the one real blocker this
configuration had. A 55-second recording — shorter than the 60 s the service
accepts — was measured end to end at **59,870 ms** on four shared vCPU (run
36566943407). That is 130 milliseconds of headroom. Past it the API answers the
operator 504 and the speech service carries on holding a CPU to finish a
transcript nobody is waiting for, and the advice the operator gets is to try
again, which starts a second one.

The 120-second cap is enforced in the service rather than merely defaulted:
`RUBAI_REQUEST_TIMEOUT_SECONDS` above it is clamped down. Queue wait and
inference share **one** deadline, so two requests behind one slot cannot add up
to twice the budget. When it runs out around the model the service answers
**504**, which the API turns into a timeout rather than a bad gateway — "too
slow" and "broken" are different things to tell someone, and only the first is
worth retrying with a shorter recording.

None of these three numbers has been measured on Railway hardware. They are
sized from a four-vCPU runner, and 150 over 120 over 60 is deliberately loose
so that a slower container does not need them changed.

On the **speech** service:

```
STT_SERVICE_TOKEN=<generate: python -c "import secrets; print(secrets.token_urlsafe(32))">
STT_LANGUAGE=uz
STT_MAX_BYTES=10485760
STT_MAX_SECONDS=60
RUBAI_CONCURRENCY=1
WHISPER_THREADS=<defaults to the container's CPU count>

# The whole-request budget: queue wait and inference together. Values above
# 120 are clamped to 120, because the API in front waits 150 and a promise
# longer than that cannot be kept to it.
RUBAI_REQUEST_TIMEOUT_SECONDS=120

# The encoder window. `auto` (the default) sizes it to each recording, so a
# short command is cheap and a long one is still heard to the end. A number
# pins every request to it — and the service then refuses to start unless
# STT_MAX_SECONDS fits inside that window, because a pinned window that cannot
# reach the end of an accepted recording loses the end of it silently.
RUBAI_AUDIO_CTX=auto         # auto | 0 (whisper's full 1500) | a number
RUBAI_AUDIO_CTX_HEADROOM_SECONDS=2
RUBAI_AUDIO_CTX_FLOOR=256

# Voice activity detection: drops silence before whisper decodes it. Off until
# measured — a detector that mistakes quiet speech for silence removes words.
RUBAI_VAD=off
```

Neither value is ever sent to the browser. The console talks to the API, the
API talks to the speech service, and every `NEXT_PUBLIC_*` value is compiled
into the bundle — so there is no version of this where a token lives there.

---

## Deploying it

The pipeline deploys this service, but only after `.github/workflows/rubai-stt.yml`
has run the guard's tests **and** built the image, started it, and made it
transcribe. That is a stronger gate than either deploy in `ci.yml` gets, and it
is there because the two things that went wrong here before — a command line
nobody could run locally, a binary tuned for the build machine's CPU — were
both invisible to tests and obvious to a container that actually starts.

It stays dormant until two things exist, and says which one is missing:

| | |
|---|---|
| `RAILWAY_TOKEN` (secret) | the same Railway project token `ci.yml` uses |
| `RAILWAY_SERVICE_STT` (variable) | this service's name or id in that project |

The service itself has to be created in Railway by hand first — a project token
can deploy services, not create them. **Empty service**, then Settings → Root
Directory `services/rubai-stt`, and **no public domain**: the whole security
argument on this page rests on the model being reachable only over Railway's
private network.

**There is no health wait in the deploy job**, which is deliberate and not an
omission. A service with no public domain has no URL for CI to poll. Railway's
own healthcheck does that job instead: `railway.json` points it at `/health`,
which stays 503 until the model answers rather than until the port opens, so a
container that cannot load its weights never takes traffic — and
`restartPolicyType: ON_FAILURE` retries it three times before giving up.

The first build on Railway fetches 514 MiB of weights and compiles
whisper.cpp, so expect it to take a good deal longer than the two minutes it
takes on a warm CI runner. That has not been measured on Railway.

Once it is up, the API is pointed at it by `STT_SERVICE_URL` and restarted.
`/api/health` then reports `stt.configured`, which says the API has a URL and a
token — not that the speech service answered. The first spoken command is what
proves that.

---

## Security

| | |
|---|---|
| Reachability | Use Railway's private address. The service has no public domain, so the model is not on the internet at all |
| Credential | Bearer token, compared with `hmac.compare_digest`. The service **refuses to start** without one unless `RUBAI_ALLOW_ANONYMOUS=true` is set deliberately |
| Size | Refused twice: by `Content-Length` before the body is read, and by the running total while it is |
| Duration | Computed from the converted WAV's own size, not from a header and not from a second process |
| Shell | None. ffmpeg is an argument list with `-nostdin` and `-protocol_whitelist file`, so a crafted container cannot make it fetch a second resource |
| Filenames | The uploaded name is used for nothing — not sanitised, unused. A name that never reaches a path or a command line cannot escape either |
| Audio | Written to a per-request temporary directory and removed in a `finally`, on every path including the refusals |
| Errors | A failure from whisper is logged by type and answered as 502; no URL, header or token reaches a log or a response |

---

## Testing

- `services/rubai-stt/tests/` — 39 tests covering the guard, with no model and
  no ffmpeg, so they run in a second on every push.
- `tests/test_voice_timeout.py` — the three waits above, as a contract: that
  they are ordered, that the slowest transcription measured fits inside them
  with room, that each kind of running out of time becomes the right status,
  and — kept rather than run once and deleted — what 60 seconds costs.
- `tests/test_voice_rubai.py` — the API's adapter against every way the service
  can fail: absent, timing out, still loading, refusing the token, returning
  500, returning a body that is not what was promised.
- `.github/workflows/rubai-stt.yml` — builds the image for real, waits for the
  model, checks that an unauthenticated caller is refused, and times
  transcription of real speech.

The CI transcription is **English** (`jfk.wav`). It proves the pipeline —
upload, ffmpeg, whisper, JSON back — and says nothing about Uzbek accuracy.
That needs real Uzbek recordings: see `docs/RUBAI_STT_BENCHMARK.md`.
