"""rubaiSTT v2 medium, behind :class:`SpeechToText`.

The model does not run in this process. It runs in `services/rubai-stt` —
whisper.cpp holding a 514 MiB Uzbek fine-tune in memory — and this adapter is
the HTTP call to it. That separation is the whole point: the API container
stays small enough to start in seconds, and a speech service that is restarting
or out of memory cannot take the agent down with it.

Two rules, the same two every other client here keeps:

1. **The token never leaves this module.** Held as a ``SecretStr``, sent only as
   a header, and stripped from anything this module raises.
2. **The response is projected, not forwarded.** The service answers with
   timings and its own diagnostics; the console gets a transcript.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Final

import httpx
from pydantic import SecretStr

from ulugbek_ai.core.errors import (
    ConfigurationError,
    SpeechError,
    SpeechTimeoutError,
)
from ulugbek_ai.voice.base import AudioContainer, SpeechToText, Transcript

logger = logging.getLogger(__name__)

#: Everything ffmpeg decodes, which is what the service converts with before
#: whisper sees it. Wider than a cloud API's list on purpose: this is where
#: Safari's ``audio/mp4`` stops being a dead end.
SUPPORTED_CONTAINERS: Final[frozenset[AudioContainer]] = frozenset(
    {
        "audio/webm",
        "audio/ogg",
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/flac",
        "audio/aac",
    }
)

_BEARER: Final[re.Pattern[str]] = re.compile(r"Bearer\s+\S+", re.IGNORECASE)


def _safe(text: str) -> str:
    return _BEARER.sub("Bearer REDACTED", text)


class RubaiSpeechToText(SpeechToText):
    """Transcription through the project's own speech service."""

    supported_containers = SUPPORTED_CONTAINERS

    def __init__(
        self,
        service_url: str,
        *,
        token: SecretStr | str | None = None,
        timeout_seconds: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = service_url.rstrip("/")
        self._token = (
            token
            if token is None or isinstance(token, SecretStr)
            else SecretStr(token)
        )
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            return {}
        return {"Authorization": f"Bearer {self._token.get_secret_value()}"}

    async def transcribe(
        self, audio: bytes, *, content_type: AudioContainer, language: str
    ) -> Transcript:
        # The service names every file it writes itself; this one is a label in
        # a multipart part and reaches no path on the other side.
        files = {"file": ("recording", audio, content_type)}
        # Whisper's auto-detect mistakes Uzbek for Arabic-script languages often
        # enough that the language is stated rather than guessed.
        data = {"language": language.split("-", 1)[0], "response_format": "json"}

        try:
            response = await self._client.post(
                f"{self._url}/inference",
                files=files,
                data=data,
                headers=self._headers(),
            )
        except httpx.TimeoutException:
            raise SpeechTimeoutError(
                "The speech service did not answer in time. A first request "
                "after a restart waits for the model to load."
            ) from None
        except httpx.HTTPError as exc:
            raise SpeechError(
                f"Could not reach the speech service: {_safe(str(exc))}"
            ) from None

        if response.status_code != 200:
            raise self._failure(response)

        return self._transcript(response.json(), language)

    def _failure(self, response: httpx.Response) -> Exception:
        """A service failure, said in terms the operator can act on."""
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(body.get("error", ""))
        except Exception:  # noqa: BLE001 - a non-JSON body is still a failure
            detail = ""

        if response.status_code == 401:
            # Ours to fix, not the caller's: the two ends disagree about a
            # credential this service was configured with.
            return ConfigurationError(
                "The speech service refused this service's token. Check that "
                "STT_SERVICE_TOKEN matches the value on the speech service."
            )
        if response.status_code == 503:
            return SpeechError(
                detail
                or "The speech service is loading its model. Try again shortly."
            )
        if response.status_code in (413, 415, 422):
            # These carry a message written for a person: which container could
            # not be decoded, or which limit was passed.
            return SpeechError(detail or "That recording could not be used.")
        return SpeechError(
            _safe(
                f"The speech service returned HTTP {response.status_code}"
                + (f": {detail}" if detail else ".")
            )
        )

    @staticmethod
    def _transcript(body: Any, language: str) -> Transcript:
        if not isinstance(body, dict) or "text" not in body:
            raise SpeechError("The speech service returned an unexpected body.")

        duration = body.get("duration_seconds")
        return Transcript(
            text=str(body["text"]).strip(),
            language=language,
            duration_seconds=(
                float(duration) if isinstance(duration, (int, float)) else None
            ),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = ["SUPPORTED_CONTAINERS", "RubaiSpeechToText"]
