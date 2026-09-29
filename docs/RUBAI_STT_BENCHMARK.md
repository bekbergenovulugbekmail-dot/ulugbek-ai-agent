# rubaiSTT benchmark

**No Uzbek speech has been measured. There are no results on this page.**

Everything below is the apparatus. It is written down so that the day someone
records twenty sentences, the numbers appear without anyone having to invent a
method — and so that nobody mistakes the model author's figures for this
deployment's.

## What is not measured, and why it matters

The model card reports ~17% WER and 5.5% CER on the author's own test set. That
is a real number about their recordings, not about yours. Word error rate moves
with the microphone, the room, the speaking rate, the dialect and the
vocabulary — and this console's vocabulary is unusual: Uzbek sentences carrying
English technical words (`deploy`, `commit`, `Railway`, project names) that no
Uzbek training corpus is dense in.

So the only number worth acting on is one measured here.

## Recording the twenty

Aim for what will actually be said to the agent, not for clean dictation:

| | |
|---|---|
| 6 short commands | "loyihalarimni ko'rsat", "oxirgi runni tekshir" |
| 6 full sentences | a request with a condition and an object in it |
| 4 with technical words | the English terms you use every day, mid-sentence |
| 2 fast, 2 quiet | how you speak when you are in a hurry or not alone |

Record them the way the console does — press the microphone button, speak, stop
— so the container and the sample rate are the real ones. Then write a manifest:

```json
[
  {"audio_id": "01", "path": "samples/01.webm",
   "reference_text": "loyihalarimni ko'rsat"},
  {"audio_id": "02", "path": "samples/02.webm",
   "reference_text": "oxirgi deploy muvaffaqiyatli bo'ldimi"}
]
```

The reference text is what you *said*, exactly, in Uzbek Latin. Apostrophes do
not need to match — the scorer normalises `oʻ`, `o'`, `o`` and the rest to one
form, so a keyboard difference is not counted as an error.

**Keep the recordings out of the repository.** They are your voice. Put them
under `samples/`, which is gitignored, or anywhere outside the tree.

## Running it

```bash
python scripts/rubai_benchmark.py samples/manifest.json \
    --service https://<the speech service>/inference \
    --token "$STT_SERVICE_TOKEN" \
    --out docs/RUBAI_STT_BENCHMARK.md
```

It posts each recording, scores it, and rewrites this file with a table of
per-recording WER, CER and latency plus mean WER, mean CER, p50 and p95
latency. A recording that fails is reported as a failure and left out of the
averages, and the header says how many of the manifest were actually measured.

Against the Railway service, run it from somewhere that can reach the private
address — or temporarily give the service a public domain, measure, and remove
it again.

## Reading the result

- **Under ~15% WER** on short commands: usable as it stands. The transcript
  goes to the command box for review anyway, so a wrong word is a keystroke.
- **15–30%**: usable, but compare against a local provider (Muxlisa, Aisha,
  UzbekVoice) before settling. `SpeechToText` exists so that is one adapter.
- **Over ~30%**: something is wrong with the pipeline rather than the model —
  check the sample rate reaching whisper, and that `language=uz` is being sent
  rather than detected.

Compare latency against the target in `docs/RUBAI_STT.md`: whisper pads every
clip to 30 seconds, so a two-second command is not cheap, and p95 is the number
that decides whether this feels like speech or like waiting.
