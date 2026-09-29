"""The adapter that talks to the speech service.

The service runs in its own container with its own failure modes — restarting,
loading a 514 MiB model, out of memory, refusing a token the two ends disagree
about. Each of those has to arrive at the console as something an operator can
act on, and none of them may carry the credential that was used.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from pydantic import SecretStr

from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.errors import (
    ConfigurationError,
    SpeechError,
    SpeechTimeoutError,
)
from ulugbek_ai.voice.factory import build_speech_to_text
from ulugbek_ai.voice.rubai import RubaiSpeechToText

SERVICE_TOKEN = "test-service-token-not-a-real-credential"
AUDIO = b"\x1a\x45\xdf\xa3" + b"\x00" * 512


def adapter(handler) -> RubaiSpeechToText:
    """An adapter wired to a service that answers however the test says."""
    return RubaiSpeechToText(
        "http://rubai-stt.railway.internal:8080",
        token=SecretStr(SERVICE_TOKEN),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# --------------------------------------------------------------------------- #
# The happy path, and what is sent
# --------------------------------------------------------------------------- #
async def test_a_transcript_comes_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/inference"
        assert request.headers["Authorization"] == f"Bearer {SERVICE_TOKEN}"
        body = request.content
        # The language is stated, not guessed: Whisper's auto-detect mistakes
        # Uzbek for Arabic-script languages often enough to matter.
        assert b'name="language"' in body and b"uz" in body
        return httpx.Response(
            200,
            json={
                "text": "  loyihalarimni ko'rsat  ",
                "language": "uz",
                "duration_seconds": 3.25,
                "latency_ms": 1800,
            },
        )

    transcript = await adapter(handler).transcribe(
        AUDIO, content_type="audio/webm", language="uz-UZ"
    )

    assert transcript.text == "loyihalarimni ko'rsat"
    assert transcript.duration_seconds == 3.25
    # The service's own timing is not part of the contract the console sees.
    assert set(transcript.__dataclass_fields__) == {
        "text",
        "language",
        "duration_seconds",
    }


async def test_safari_audio_is_no_longer_a_dead_end() -> None:
    """ffmpeg runs inside the service, so MP4 is just another container.

    Google's API could not read it, which is why the console used to hide the
    microphone on Safari entirely.
    """
    assert "audio/mp4" in RubaiSpeechToText.supported_containers

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "salom", "duration_seconds": 1})

    transcript = await adapter(handler).transcribe(
        AUDIO, content_type="audio/mp4", language="uz-UZ"
    )

    assert transcript.text == "salom"


# --------------------------------------------------------------------------- #
# Every way the service can fail
# --------------------------------------------------------------------------- #
async def test_a_service_that_is_not_there() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("all connection attempts failed")

    with pytest.raises(SpeechError) as caught:
        await adapter(handler).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    assert "speech service" in str(caught.value).lower()


async def test_a_service_that_does_not_answer_in_time() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(SpeechTimeoutError) as caught:
        await adapter(handler).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    # The first request after a restart waits for the model, and saying so
    # turns a mysterious timeout into an explained one.
    assert "model to load" in str(caught.value)


async def test_a_service_still_loading_its_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "The model is still loading."})

    with pytest.raises(SpeechError) as caught:
        await adapter(handler).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    assert "still loading" in str(caught.value)


async def test_a_token_the_two_ends_disagree_about() -> None:
    """Not an authentication failure for the caller — a misconfiguration here."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "This service requires a token."})

    with pytest.raises(ConfigurationError) as caught:
        await adapter(handler).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    assert "STT_SERVICE_TOKEN" in str(caught.value)
    assert SERVICE_TOKEN not in str(caught.value)


async def test_a_service_that_breaks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="<html>Internal Server Error</html>")

    with pytest.raises(SpeechError) as caught:
        await adapter(handler).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    assert "500" in str(caught.value)


async def test_a_body_that_is_not_what_was_promised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": "surprise"})

    with pytest.raises(SpeechError) as caught:
        await adapter(handler).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    assert "unexpected body" in str(caught.value)


async def test_a_refused_recording_keeps_the_service_s_own_explanation() -> None:
    """415 and 413 carry a message written for a person; do not replace it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            415, json={"error": "That recording could not be decoded."}
        )

    with pytest.raises(SpeechError) as caught:
        await adapter(handler).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    assert "could not be decoded" in str(caught.value)


# --------------------------------------------------------------------------- #
# The token stays here
# --------------------------------------------------------------------------- #
async def test_a_token_echoed_back_in_a_json_error_is_redacted() -> None:
    """A service that repeats the header back must not get it to the console."""
    response = httpx.Response(
        418, json={"error": f"Authorization: Bearer {SERVICE_TOKEN}"}
    )

    with pytest.raises(SpeechError) as caught:
        await adapter(lambda request: response).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    assert SERVICE_TOKEN not in str(caught.value)
    assert "REDACTED" in str(caught.value)


async def test_a_body_that_is_not_json_is_dropped_rather_than_relayed() -> None:
    """An HTML error page from a proxy is not a message for anyone.

    Nothing is quoted from it at all, which is a stronger guarantee than
    redacting it: there is no pattern to miss.
    """
    response = httpx.Response(500, text=f"failed for Bearer {SERVICE_TOKEN}")

    with pytest.raises(SpeechError) as caught:
        await adapter(lambda request: response).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    assert str(caught.value) == "The speech service returned HTTP 500."


def test_the_token_is_not_logged_when_the_provider_is_built(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        stt_provider="rubai",
        stt_service_url="http://rubai-stt.railway.internal:8080",
        stt_service_token=SERVICE_TOKEN,
    )

    with caplog.at_level(logging.DEBUG):
        provider = build_speech_to_text(settings)

    assert isinstance(provider, RubaiSpeechToText)
    assert SERVICE_TOKEN not in caplog.text
    # The host is useful when a microphone is silent; the credential is not.
    assert "rubai-stt.railway.internal" in caplog.text


# --------------------------------------------------------------------------- #
# Which provider a configuration asks for
# --------------------------------------------------------------------------- #
def test_no_service_url_means_no_provider() -> None:
    settings = Settings(_env_file=None, environment="test", stt_provider="rubai")

    # Missing, not broken: the service comes up and the endpoint says which
    # variable to set, exactly as it does for a missing model key.
    assert build_speech_to_text(settings) is None


def test_google_remains_available_as_an_alternative() -> None:
    from ulugbek_ai.voice.google import GoogleSpeechToText

    settings = Settings(
        _env_file=None,
        environment="test",
        stt_provider="google",
        stt_api_key="test-google-key-not-a-real-credential",
    )

    assert isinstance(build_speech_to_text(settings), GoogleSpeechToText)


def test_disabled_means_disabled() -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        stt_provider="disabled",
        stt_service_url="http://rubai-stt.railway.internal:8080",
    )

    assert build_speech_to_text(settings) is None
