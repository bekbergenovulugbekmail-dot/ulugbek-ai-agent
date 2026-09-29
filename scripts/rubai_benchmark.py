#!/usr/bin/env python3
"""Measure rubaiSTT on real Uzbek speech.

Vendor accuracy claims do not survive contact with a particular microphone, a
particular room and a particular set of words, and Uzbek is low-resource enough
that the gap can be large. This script produces the only number worth acting
on: the error rate on recordings the operator actually made.

    python scripts/rubai_benchmark.py samples/manifest.json \\
        --service https://<the speech service>/inference \\
        --token "$STT_SERVICE_TOKEN" \\
        --out docs/RUBAI_STT_BENCHMARK.md

The manifest is a JSON list, one object per recording:

    [{"audio_id": "01", "path": "samples/01.webm",
      "reference_text": "loyihalarimni ko'rsat"}]

Nothing is invented: a recording that fails is reported as a failure and left
out of the averages, and the report says how many were measured.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import httpx

#: Uzbek Latin writes oʻ and gʻ, and a keyboard produces any of half a dozen
#: marks for that. Counting those as errors would measure the keyboard.
_APOSTROPHES = dict.fromkeys(map(ord, "‘’ʻʼʽ`´"), "'")
_PUNCTUATION = re.compile(r"[^\w\s']", re.UNICODE)


def normalise(text: str) -> str:
    """Casefold, unify apostrophes, drop punctuation, collapse whitespace."""
    text = unicodedata.normalize("NFC", text).translate(_APOSTROPHES)
    text = _PUNCTUATION.sub(" ", text.casefold())
    return " ".join(text.split())


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    """Levenshtein, two rows at a time."""
    if not reference:
        return len(hypothesis)
    previous = list(range(len(reference) + 1))
    for j, symbol in enumerate(hypothesis, start=1):
        current = [j]
        for i, expected in enumerate(reference, start=1):
            current.append(
                min(
                    previous[i] + 1,  # deletion
                    current[i - 1] + 1,  # insertion
                    previous[i - 1] + (symbol != expected),  # substitution
                )
            )
        previous = current
    return previous[-1]


def error_rate(reference: str, hypothesis: str, *, by: str) -> float | None:
    """Word or character error rate, or ``None`` when there is nothing to score."""
    ref = normalise(reference)
    hyp = normalise(hypothesis)
    units_ref = ref.split() if by == "word" else list(ref.replace(" ", ""))
    units_hyp = hyp.split() if by == "word" else list(hyp.replace(" ", ""))
    if not units_ref:
        return None
    return edit_distance(units_ref, units_hyp) / len(units_ref)


@dataclass
class Result:
    audio_id: str
    duration: float | None
    reference_text: str
    rubai_text: str
    wer: float | None
    cer: float | None
    latency_ms: int
    error: str | None = None


def transcribe(
    client: httpx.Client, service: str, path: Path, token: str | None
) -> tuple[dict[str, Any], int]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with path.open("rb") as handle:
        started = time.monotonic()
        response = client.post(
            service,
            files={"file": (path.name, handle, "application/octet-stream")},
            data={"language": "uz", "response_format": "json"},
            headers=headers,
        )
        latency = int((time.monotonic() - started) * 1000)
    response.raise_for_status()
    return response.json(), latency


def run(manifest: list[dict[str, Any]], service: str, token: str | None) -> list[Result]:
    results: list[Result] = []
    with httpx.Client(timeout=180.0) as client:
        for entry in manifest:
            audio_id = str(entry.get("audio_id", "?"))
            path = Path(entry["path"])
            reference = str(entry.get("reference_text", ""))
            if not path.exists():
                results.append(
                    Result(audio_id, None, reference, "", None, None, 0, "file missing")
                )
                continue
            try:
                body, latency = transcribe(client, service, path, token)
            except Exception as exc:  # noqa: BLE001 - a failure is a result
                results.append(
                    Result(
                        audio_id, None, reference, "", None, None, 0, type(exc).__name__
                    )
                )
                continue
            text = str(body.get("text", ""))
            results.append(
                Result(
                    audio_id=audio_id,
                    duration=body.get("duration_seconds"),
                    reference_text=reference,
                    rubai_text=text,
                    wer=error_rate(reference, text, by="word"),
                    cer=error_rate(reference, text, by="char"),
                    latency_ms=latency,
                )
            )
    return results


def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return ordered[index]


def report(results: list[Result], service: str) -> str:
    measured = [r for r in results if r.error is None and r.wer is not None]
    failed = [r for r in results if r.error is not None]
    lines = [
        "# rubaiSTT benchmark",
        "",
        f"Measured on {time.strftime('%Y-%m-%d')} against `{service.split('://')[-1].split('/')[0]}`.",
        "",
        f"- recordings in the manifest: **{len(results)}**",
        f"- transcribed: **{len(measured)}**",
        f"- failed: **{len(failed)}**",
        "",
    ]
    if measured:
        wers = [r.wer for r in measured if r.wer is not None]
        cers = [r.cer for r in measured if r.cer is not None]
        latencies = [float(r.latency_ms) for r in measured]
        lines += [
            "| metric | value |",
            "|---|---|",
            f"| WER (mean) | {statistics.fmean(wers):.1%} |",
            f"| CER (mean) | {statistics.fmean(cers):.1%} |",
            f"| latency p50 | {percentile(latencies, 0.5):.0f} ms |",
            f"| latency p95 | {percentile(latencies, 0.95):.0f} ms |",
            "",
        ]
    else:
        lines += [
            "**Nothing was measured.** No result below is an accuracy claim.",
            "",
        ]

    lines += [
        "| audio | duration | latency | WER | CER | reference → transcript |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.error:
            lines.append(f"| {r.audio_id} | — | — | — | — | **{r.error}** |")
            continue
        duration = f"{r.duration:.1f}s" if r.duration else "—"
        lines.append(
            f"| {r.audio_id} | {duration} | {r.latency_ms} ms | "
            f"{r.wer:.1%} | {r.cer:.1%} | "
            f"`{r.reference_text}` → `{r.rubai_text}` |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--service", required=True, help="the /inference URL")
    parser.add_argument("--token", default=None, help="STT_SERVICE_TOKEN")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())
    if not isinstance(manifest, list) or not manifest:
        print("The manifest must be a non-empty JSON list.", file=sys.stderr)
        return 2

    results = run(manifest, args.service, args.token)
    text = report(results, args.service)
    if args.out:
        args.out.write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0 if any(r.error is None for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
