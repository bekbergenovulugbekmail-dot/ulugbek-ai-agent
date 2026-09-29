"""Which speech provider this configuration asks for.

One function, one branch per provider. Adding Azure means adding a case here
and an adapter beside :mod:`ulugbek_ai.voice.google` — no route, no dependency
and no frontend path changes with it.
"""

from __future__ import annotations

import logging

from ulugbek_ai.config.settings import Settings
from ulugbek_ai.voice.base import SpeechToText
from ulugbek_ai.voice.google import GoogleSpeechToText

logger = logging.getLogger(__name__)


def build_speech_to_text(settings: Settings) -> SpeechToText | None:
    """The configured transcription provider, or ``None`` when there is none.

    ``None`` rather than an exception, for the same reason a missing Anthropic
    key does not stop the service starting: the console should come up and say
    which variable is missing, not fail to boot.
    """
    if settings.stt_provider == "disabled":
        return None
    if settings.stt_api_key is None:
        return None

    if settings.stt_provider == "google":
        logger.info("Speech-to-text: google (%s)", settings.stt_language)
        return GoogleSpeechToText(
            settings.stt_api_key,
            api_url=settings.stt_api_url,
            timeout_seconds=settings.stt_timeout_seconds,
        )

    # Unreachable while the setting is a Literal, and cheap insurance for the
    # day it stops being one.
    logger.warning(  # pragma: no cover
        "Unknown STT_PROVIDER %r; speech is off.", settings.stt_provider
    )
    return None  # pragma: no cover


__all__ = ["build_speech_to_text"]
