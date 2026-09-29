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

### So: not the default, and now impossible to set unsafely

`RUBAI_AUDIO_CTX` stays **0**. Audio past the shortened window is not
transcribed badly — it is not transcribed at all, and the request still
succeeds with a plausible-looking transcript that is missing its end. Making
that the default while `STT_MAX_SECONDS` accepts 60 would be shipping silent
data loss in exchange for a latency number.

The service now **refuses to start** when the context cannot reach the end of a
recording it would accept, naming both variables. So the way to take the 50% is
to take it deliberately:

```
RUBAI_AUDIO_CTX=768
STT_MAX_SECONDS=15        # 768 / 50 — the service checks this arithmetic
```

Fifteen seconds is a long spoken command, so for this console that may well be
the right trade. It is a decision about what the product accepts, not a tuning
knob, which is why it is not made here.

### The next candidate: VAD

Not implemented, and not measured. whisper.cpp v1.9.4 ships voice activity
detection (`--vad`, with `--vad-model`). It addresses the same waste from the
other end: instead of shortening the window, it drops the silence inside it, so
a two-second command carries two seconds of audio into the encoder rather than
thirty. It should compose with a shortened context rather than compete with it,
and it would likely also fix `musiqa` — there is nothing to hallucinate over if
the silence never reaches the model.

It needs a second model file (a few MB), a second pinned checksum, and its own
run of this harness. Worth doing next; not done here.

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
RUBAI_AUDIO_CTX=0            # 768 halves the latency but hears only 15.4s;
                             # the service refuses to start unless
                             # STT_MAX_SECONDS is lowered to match
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
