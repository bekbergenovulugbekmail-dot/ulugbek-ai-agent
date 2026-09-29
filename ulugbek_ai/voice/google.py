"""Google Cloud Speech-to-Text, behind :class:`SpeechToText`.

Chosen for Uzbek because ``uz-UZ`` is a locale Google supports outright rather
than a language a multilingual model happens to cope with. Whether it is the
*best* choice for this operator's voice is an empirical question — that is what
the interface is for.

Two rules, the same two the GitHub client keeps:

1. **The key never leaves this module.** It is held as a ``SecretStr``, sent
   only as a query parameter, and stripped from every error this module raises
   before that error can reach a log or an HTTP response.
2. **The response is projected, not forwarded.** Google returns alternatives,
   confidences, word timings and request metadata; the console needs a string.
"""

from __future__ import annotations

import base64
import logging
import re
from typing import Any, Final

import httpx
from pydantic import SecretStr

from ulugbek_ai.core.errors import SpeechError, SpeechTimeoutError
from ulugbek_ai.voice.base import AudioContainer, SpeechToText, Transcript

logger = logging.getLogger(__name__)

DEFAULT_API_URL: Final[str] = "https://speech.googleapis.com/v1/speech:recognize"

#: Browser container → Google's encoding name.
#:
#: Only containers that carry their own sample rate are here. ``LINEAR16`` would
#: also work, but it needs ``sampleRateHertz`` supplied alongside, and a guessed
#: sample rate produces a transcript that is wrong in a way nobody can see.
#:
#: ``audio/mp4`` is deliberately absent. Safari's ``MediaRecorder`` produces it
#: and Google cannot decode it; saying so by name is more use than forwarding it
#: and relaying a provider error about an opaque blob.
ENCODINGS: Final[dict[AudioContainer, str]] = {
    "audio/webm": "WEBM_OPUS",
    "audio/ogg": "OGG_OPUS",
    "audio/flac": "FLAC",
}

#: ``key=<secret>`` anywhere in a message a provider or httpx built for us.
_KEY_IN_TEXT: Final[re.Pattern[str]] = re.compile(r"key=[^&\s\"']+")


def _safe(text: str) -> str:
    """A provider message with any key query parameter removed."""
    return _KEY_IN_TEXT.sub("key=REDACTED", text)


class GoogleSpeechToText(SpeechToText):
    """Transcription through ``speech:recognize``."""

    supported_containers = frozenset(ENCODINGS)

    def __init__(
        self,
        api_key: SecretStr | str,
        *,
        api_url: str = DEFAULT_API_URL,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = (
            api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        )
        self._api_url = api_url
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    async def transcribe(
        self, audio: bytes, *, content_type: AudioContainer, language: str
    ) -> Transcript:
        encoding = ENCODINGS.get(content_type)
        if encoding is None:  # pragma: no cover - the router refuses these first
            raise SpeechError(
                f"{content_type} is not a container this provider can read."
            )

        payload: dict[str, Any] = {
            "config": {
                "encoding": encoding,
                "languageCode": language,
                # One alternative: the console shows the transcript for the
                # operator to correct, so a list of near-misses would be noise.
                "maxAlternatives": 1,
                "enableAutomaticPunctuation": True,
            },
            # Google takes the audio inline as base64. The 33% overhead is paid
            # on one hop inside the data centre, not on the operator's uplink.
            "audio": {"content": base64.b64encode(audio).decode("ascii")},
        }

        try:
            response = await self._client.post(
                self._api_url,
                params={"key": self._api_key.get_secret_value()},
                json=payload,
            )
        except httpx.TimeoutException:
            raise SpeechTimeoutError(
                "The speech provider did not answer in time."
            ) from None
        except httpx.HTTPError as exc:
            raise SpeechError(
                f"Could not reach the speech provider: {_safe(str(exc))}"
            ) from None

        if response.status_code != 200:
            raise SpeechError(self._failure(response))

        return self._transcript(response.json(), language)

    @staticmethod
    def _failure(response: httpx.Response) -> str:
        """A provider failure, said plainly and without the key.

        401 and 403 are the ones an operator can actually fix, so they are named
        rather than folded into a generic transport error.
        """
        detail = ""
        try:
            error = response.json().get("error", {})
            detail = str(error.get("message", ""))
        except Exception:  # noqa: BLE001 - a non-JSON body is still a failure
            detail = ""

        if response.status_code in (401, 403):
            return (
                "The speech provider refused the credential. Check STT_API_KEY "
                "and that the Speech-to-Text API is enabled for that key."
            )
        if response.status_code == 429:
            return "The speech provider is rate limiting this key. Try again shortly."
        return _safe(
            f"The speech provider returned HTTP {response.status_code}"
            + (f": {detail}" if detail else ".")
        )

    @staticmethod
    def _transcript(body: dict[str, Any], language: str) -> Transcript:
        """The first alternative of every result, joined.

        Google segments long audio into several results; the console wants one
        string. Silence comes back as no results at all, which is an empty
        transcript rather than an error — the operator hears nothing back and
        can simply speak again.
        """
        parts: list[str] = []
        for result in body.get("results") or []:
            alternatives = result.get("alternatives") or []
            if alternatives:
                parts.append(str(alternatives[0].get("transcript", "")).strip())

        billed = body.get("totalBilledTime")
        duration: float | None = None
        if isinstance(billed, str) and billed.endswith("s"):
            try:
                duration = float(billed[:-1])
            except ValueError:
                duration = None

        return Transcript(
            text=" ".join(part for part in parts if part).strip(),
            language=language,
            duration_seconds=duration,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = ["DEFAULT_API_URL", "ENCODINGS", "GoogleSpeechToText"]
