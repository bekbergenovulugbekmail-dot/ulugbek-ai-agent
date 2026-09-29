"""Voice endpoints.

Two of them, and deliberately no more. Speech is a shell around the text path
that already exists — audio becomes text *before* a run starts, and an answer
becomes audio *after* one finishes — so nothing here touches the agent, the
event stream or the database. A provider outage costs the console its
microphone and nothing else.

The transcript is returned to the operator rather than sent onward. Uzbek is a
low-resource language and every transcriber gets it wrong sometimes; a run
started from an unreviewed transcript is a run that acts on a sentence nobody
said. The console puts the text in the command box and waits.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from ulugbek_ai.api.deps import SettingsDep, SpeechToTextDep
from ulugbek_ai.core.errors import (
    ConfigurationError,
    PayloadTooLargeError,
    SpeechError,
    UlugbekError,
    UnsupportedMediaTypeError,
    ValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])


class TranscriptRead(BaseModel):
    """What the console puts in the command box."""

    text: str
    #: The primary subtag of the locale the provider was asked for. The console
    #: has no use for the region, and reporting less is the habit worth keeping.
    language: str
    #: ``None`` when the provider does not say. A number derived from the byte
    #: count would look like a measurement and be a guess.
    duration_seconds: float | None = None


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5_000)
    voice: Literal["female", "male"] = "female"


def _container(content_type: str | None) -> str:
    """The bare media type. ``audio/webm;codecs=opus`` is what browsers send."""
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


@router.post(
    "/transcribe",
    response_model=TranscriptRead,
    summary="Turn recorded audio into text",
)
async def transcribe(
    request: Request,
    stt: SpeechToTextDep,
    settings: SettingsDep,
    content_type: Annotated[str | None, Header()] = None,
    content_length: Annotated[int | None, Header()] = None,
    x_audio_duration_seconds: Annotated[float | None, Header()] = None,
) -> TranscriptRead:
    """Transcribe one recording.

    The body is the container the browser recorded, sent raw. Not multipart,
    which would need a dependency this service does not otherwise have, and not
    base64, which would add a third of the size to the operator's uplink for
    nothing.
    """
    container = _container(content_type)
    if container not in stt.supported_containers:
        accepted = ", ".join(sorted(stt.supported_containers))
        raise UnsupportedMediaTypeError(
            f"This provider cannot read {container or 'an unnamed container'}. "
            f"It accepts: {accepted}.",
            details={"accepted": sorted(stt.supported_containers)},
        )

    # Two size checks, and both earn their place. The header lets a request be
    # refused before its body is read at all; the measured length is what is
    # actually enforced, because a header can say anything.
    if content_length is not None and content_length > settings.stt_max_bytes:
        raise PayloadTooLargeError(
            f"That recording is larger than the {settings.stt_max_bytes} byte "
            "limit. Record something shorter."
        )

    # Trusted only to save a provider call, never as the limit itself.
    if (
        x_audio_duration_seconds is not None
        and x_audio_duration_seconds > settings.stt_max_seconds
    ):
        raise PayloadTooLargeError(
            f"That recording is longer than the {settings.stt_max_seconds:g} "
            "second limit. Record something shorter."
        )

    audio = await request.body()
    if not audio:
        raise ValidationError("No audio was sent.")
    if len(audio) > settings.stt_max_bytes:
        raise PayloadTooLargeError(
            f"That recording is larger than the {settings.stt_max_bytes} byte "
            "limit. Record something shorter."
        )

    try:
        transcript = await stt.transcribe(
            audio, content_type=container, language=settings.stt_language
        )
    except UlugbekError:
        # Already a domain error: its message was built to be shown.
        raise
    except Exception as exc:  # noqa: BLE001 - an adapter bug is still a failure
        # The type only. Not the message, not a traceback: an HTTP library puts
        # the full request URL in both, and for this provider the key is in the
        # query string. There is no redaction pass that can be trusted more
        # than simply not writing it down.
        logger.error("Speech provider failed: %s", type(exc).__name__)
        raise SpeechError("The speech provider failed.") from None

    return TranscriptRead(
        text=transcript.text,
        language=settings.stt_language.split("-", 1)[0],
        duration_seconds=transcript.duration_seconds,
    )


@router.post("/speak", summary="Turn an answer into audio")
async def speak(payload: SpeakRequest, settings: SettingsDep) -> None:
    """Synthesise an answer.

    No server-side voice is implemented: the console speaks through the
    browser's own ``SpeechSynthesis``, which needs no key, costs nothing and
    sends no audio across the network. The endpoint exists so the seam is real
    and guarded — adding Azure, which has genuine ``uz-UZ`` neural voices, is an
    adapter behind ``TextToSpeech`` and a branch here, with no new route and no
    new frontend path.
    """
    raise ConfigurationError(
        f"TTS_PROVIDER is {settings.tts_provider!r}: the console synthesises "
        "speech in the browser and this service produces no audio. Configure a "
        "server-side voice provider to receive audio from this endpoint."
    )
