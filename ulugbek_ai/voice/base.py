"""Provider-agnostic speech interfaces.

The API talks to :class:`SpeechToText` and :class:`TextToSpeech` only. Whatever
a provider's wire format happens to be — Google's base64 JSON, Azure's SSML,
someone's multipart upload — stays inside that provider's adapter, exactly as
Anthropic's shapes stay inside :mod:`ulugbek_ai.llm.claude`.

This matters more here than it does for the model. Uzbek is a low-resource
language: which provider hears it best is an empirical question that cannot be
settled by reading marketing pages, so the answer has to be replaceable in one
file rather than threaded through the routes.

The audio itself never appears in these types. It arrives as bytes, is handed
to a provider, and is gone when the request ends — nothing here can hold it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

#: Containers a provider may be asked to read, mapped from the ``Content-Type``
#: a browser's ``MediaRecorder`` produces. The router refuses anything else by
#: name, which is a better answer than a provider error about an opaque blob.
AudioContainer = str


@dataclass(slots=True)
class Transcript:
    """What the console gets back. Deliberately three fields.

    The provider's raw response stays in the adapter: it carries request ids,
    quota counters and sometimes the key that was used, and none of that has
    any business crossing into an HTTP response.
    """

    text: str
    language: str
    #: How long the provider says the audio was, when it says. ``None`` is an
    #: honest answer; a number computed from the byte count would not be.
    duration_seconds: float | None = None


@dataclass(slots=True)
class Speech:
    """Synthesised audio, ready to stream to the browser."""

    audio: bytes
    media_type: str


class SpeechToText(ABC):
    """Interface every transcription provider implements."""

    #: Containers this provider can decode, as bare media types.
    supported_containers: frozenset[AudioContainer] = frozenset()

    @abstractmethod
    async def transcribe(
        self, audio: bytes, *, content_type: AudioContainer, language: str
    ) -> Transcript:
        """Turn audio into text.

        Args:
            audio: The raw container bytes, exactly as the browser recorded them.
            content_type: The bare media type, parameters already stripped.
            language: A BCP-47 tag such as ``uz-UZ``.

        Raises:
            SpeechError: on any provider failure, already redacted.
        """

    async def aclose(self) -> None:
        """Release provider resources. Safe to call more than once."""
        return None


class TextToSpeech(ABC):
    """Interface every synthesis provider implements.

    Nothing implements this on the server yet: the console speaks through the
    browser's own ``SpeechSynthesis``, which costs nothing and needs no key. The
    interface exists so that adding a server voice — Azure has real ``uz-UZ``
    neural voices — is one adapter and one settings branch, not a new endpoint
    and a new frontend path.
    """

    @abstractmethod
    async def synthesize(self, text: str, *, voice: str) -> Speech:
        """Turn text into audio.

        Raises:
            SpeechError: on any provider failure, already redacted.
        """

    async def aclose(self) -> None:
        return None


__all__ = [
    "AudioContainer",
    "Speech",
    "SpeechToText",
    "TextToSpeech",
    "Transcript",
]
