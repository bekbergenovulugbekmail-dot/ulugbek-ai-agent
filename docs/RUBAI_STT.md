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

The licence chain is Apache-2.0 → Apache-2.0 → MIT, with the conversion's card
naming its base model, so the whole stack is redistributable.

**A build cannot drift.** The Dockerfile fetches the file at that commit,
checks the sha256, and checks the first four bytes are the GGML magic — an HTML
error page saved under the right name passes every check but the last.

The conversion's author states that q5_0 produced identical text to q8_0 on
their Uzbek test sentences, which is why only q5_0 is published. That claim has
not been re-tested here.

---

## Resources

Measured on a GitHub runner (4 vCPU) by `.github/workflows/rubai-stt.yml`,
which builds the image and times real requests. See the workflow's latest run
for current numbers; it prints image size, time-to-ready, per-request latency
and container memory.

What the shape of the problem dictates, regardless of the machine:

- **Whisper pads every clip to 30 seconds.** A three-second command costs the
  same encoder pass as a thirty-second one, so latency does not shrink with
  short audio. This is the single most surprising thing about the cost model.
- **One inference at a time.** `RUBAI_CONCURRENCY` defaults to 1: the working
  set is held for the length of an inference, two at once doubles it on a
  container sized for one, and the second request is no faster for having
  started earlier. Past the limit the service answers 503 rather than swapping.
- **Cold start is a model load**, not a container start. The weights are baked
  into the image, so it is a read from local disk — but the health endpoint
  stays 503 until the model has answered a warm-up request, so Railway does not
  route traffic at a container that cannot serve it.

**If the plan cannot carry it.** The smallest step down is `q4_0` (~400 MiB,
lower accuracy), then Whisper *small* (~250 MiB, noticeably worse on Uzbek),
then back to a cloud API for the audio while keeping this architecture — the
provider is one settings line. Scaling the service to zero between uses trades
money for a cold start on the first command of the day.

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
STT_TIMEOUT_SECONDS=60
```

On the **speech** service:

```
STT_SERVICE_TOKEN=<generate: python -c "import secrets; print(secrets.token_urlsafe(32))">
STT_LANGUAGE=uz
STT_MAX_BYTES=10485760
STT_MAX_SECONDS=60
RUBAI_CONCURRENCY=1
WHISPER_THREADS=<defaults to the container's CPU count>
```

Neither value is ever sent to the browser. The console talks to the API, the
API talks to the speech service, and every `NEXT_PUBLIC_*` value is compiled
into the bundle — so there is no version of this where a token lives there.

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

- `services/rubai-stt/tests/` — 18 tests covering the guard, with no model and
  no ffmpeg, so they run in a second on every push.
- `tests/test_voice_rubai.py` — the API's adapter against every way the service
  can fail: absent, timing out, still loading, refusing the token, returning
  500, returning a body that is not what was promised.
- `.github/workflows/rubai-stt.yml` — builds the image for real, waits for the
  model, checks that an unauthenticated caller is refused, and times
  transcription of real speech.

The CI transcription is **English** (`jfk.wav`). It proves the pipeline —
upload, ffmpeg, whisper, JSON back — and says nothing about Uzbek accuracy.
That needs real Uzbek recordings: see `docs/RUBAI_STT_BENCHMARK.md`.
