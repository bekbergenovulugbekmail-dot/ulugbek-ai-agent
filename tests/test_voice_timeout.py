"""The timeout contract between the console, the API and the speech service.

Three waits sit in a line, and they only work if each one is longer than the
one inside it:

    the API waits  STT_TIMEOUT_SECONDS = 150s
      the service's whole answer is capped at  120s   (MAX_REQUEST_BUDGET)
        the audio it will accept at all is at most  STT_MAX_SECONDS = 60s

Get that order wrong and the failure is not a crash. The API answers 504 while
the speech service keeps a CPU busy finishing a transcript nobody is waiting
for, and the operator is told to try again -- which starts a second one.

The number that forced this: on four shared vCPU a 55-second recording was
measured end to end at 59,870 ms (CI run 36566943407). Against the previous
60-second client timeout that is 130 ms of headroom, for a recording *shorter*
than STT_MAX_SECONDS actually accepts.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from tests.conftest import OPERATOR_TOKEN
from tests.conftest import test_database_url as _database_url
from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.errors import SpeechError, SpeechTimeoutError
from ulugbek_ai.voice.factory import build_speech_to_text
from ulugbek_ai.voice.rubai import RubaiSpeechToText

#: Measured, not estimated: the slowest real transcription this project has a
#: number for. CI run 36566943407, `speech-55s.wav`, four shared vCPU.
MEASURED_55_SECOND_TRANSCRIPTION: float = 59.870

#: The ceiling the speech service clamps its own request budget to. Kept here
#: as a literal on purpose — the two services deploy separately, so this file
#: is asserting what the API *believes* about the other side, and a change
#: there that this does not know about should break something.
SERVICE_INTERNAL_BUDGET: float = 120.0

SERVICE_TOKEN = "test-service-token-not-a-real-credential"
AUDIO = b"\x1a\x45\xdf\xa3" + b"\x00" * 512


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        environment="test",
        auth_token=OPERATOR_TOKEN,
        database_url=_database_url(),
        stt_provider="rubai",
        stt_service_url="http://rubai-stt.railway.internal:8080",
        stt_service_token=SecretStr(SERVICE_TOKEN),
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def adapter(handler) -> RubaiSpeechToText:
    return RubaiSpeechToText(
        "http://rubai-stt.railway.internal:8080",
        token=SecretStr(SERVICE_TOKEN),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# --------------------------------------------------------------------------- #
# A) The waits are in the right order
# --------------------------------------------------------------------------- #
def test_the_api_waits_longer_than_the_service_can_take() -> None:
    settings = _settings()

    assert settings.stt_timeout_seconds == 150.0
    assert settings.stt_timeout_seconds > SERVICE_INTERNAL_BUDGET
    assert SERVICE_INTERNAL_BUDGET > settings.stt_max_seconds


def test_the_slowest_transcription_measured_fits_with_room_to_spare() -> None:
    """A 55-second recording is inside the wait, not balanced on its edge."""
    settings = _settings()

    assert settings.stt_timeout_seconds > MEASURED_55_SECOND_TRANSCRIPTION
    # Not "fits" but "fits comfortably": the measurement came off a shared
    # runner and Railway is its own machine again.
    assert settings.stt_timeout_seconds >= 2 * MEASURED_55_SECOND_TRANSCRIPTION


def test_sixty_seconds_is_what_this_contract_exists_to_rule_out() -> None:
    """The negative control, kept rather than run once and thrown away.

    Setting the client timeout back to 60 is still allowed — it is a
    configurable — so what stops it coming back by accident is this test
    saying, in numbers, what it costs: a recording the service will happily
    accept, transcribed in a time the client has already stopped waiting for.
    """
    dangerous = _settings(stt_timeout_seconds=60)

    headroom = dangerous.stt_timeout_seconds - MEASURED_55_SECOND_TRANSCRIPTION
    assert 0 < headroom < 1.0  # 130 milliseconds, measured

    # And the recording that used it all up is not even the longest one this
    # configuration accepts.
    assert dangerous.stt_max_seconds > 55.0

    # The client would give up first, leaving the service working alone.
    assert dangerous.stt_timeout_seconds < SERVICE_INTERNAL_BUDGET


def test_the_wait_the_settings_name_is_the_wait_the_client_uses() -> None:
    """A default nothing reads is a comment.

    The timeout has to arrive at the httpx client the adapter actually calls
    through, which is a different statement from the setting having a value.
    """
    provider = build_speech_to_text(_settings())

    assert isinstance(provider, RubaiSpeechToText)
    assert provider._client.timeout.read == 150.0
    assert provider._client.timeout.connect == 150.0


# --------------------------------------------------------------------------- #
# D + E) What each kind of running out of time becomes
# --------------------------------------------------------------------------- #
async def test_a_service_that_never_answers_is_a_gateway_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(SpeechTimeoutError) as caught:
        await adapter(handler).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    assert caught.value.http_status == 504


async def test_a_service_that_ran_out_of_its_own_budget_is_also_a_timeout() -> None:
    """504 from the speech service is a timeout, not a generic bad gateway.

    The service answers 504 when the model outran the budget it was given.
    Folding that into the catch-all would tell the operator the model broke,
    when what happened is that it was too slow — different advice, and the only
    one of the two where trying again with a shorter recording helps.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            504, json={"error": "The model took too long to transcribe this."}
        )

    with pytest.raises(SpeechTimeoutError) as caught:
        await adapter(handler).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    assert caught.value.http_status == 504
    assert "too long" in str(caught.value)


async def test_a_broken_model_is_still_a_bad_gateway_not_a_timeout() -> None:
    """The other half of the same statement, or the one above proves nothing."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502, json={"error": "The model failed to transcribe the audio."}
        )

    with pytest.raises(SpeechError) as caught:
        await adapter(handler).transcribe(
            AUDIO, content_type="audio/webm", language="uz-UZ"
        )

    assert not isinstance(caught.value, SpeechTimeoutError)
    assert caught.value.http_status == 502


# --------------------------------------------------------------------------- #
# C + D) End to end through the route
# --------------------------------------------------------------------------- #
def _voice_app(database, settings: Settings, llm, registry, stt):
    from ulugbek_ai.api.deps import database_dependency, session_dependency
    from ulugbek_ai.main import create_app

    app = create_app(settings)

    async def override_session():
        async with database.session() as session:
            yield session

    app.dependency_overrides[session_dependency] = override_session
    app.dependency_overrides[database_dependency] = lambda: database
    app.state.llm = llm
    app.state.registry = registry
    app.state.stt = stt
    return app


async def _client(database, settings, llm, registry, handler):
    stt = adapter(handler)
    app = _voice_app(database, settings, llm, registry, stt)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"},
    )


async def test_a_timed_out_transcription_reaches_the_console_as_504(
    database, llm, registry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    http = await _client(database, _settings(), llm, registry, handler)
    async with http:
        response = await http.post(
            "/api/voice/transcribe",
            content=AUDIO,
            headers={"Content-Type": "audio/webm;codecs=opus"},
        )

    assert response.status_code == 504
    assert SERVICE_TOKEN not in response.text


async def test_the_service_s_own_504_reaches_the_console_as_504(
    database, llm, registry
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            504, json={"error": "The model took too long to transcribe this."}
        )

    http = await _client(database, _settings(), llm, registry, handler)
    async with http:
        response = await http.post(
            "/api/voice/transcribe",
            content=AUDIO,
            headers={"Content-Type": "audio/webm;codecs=opus"},
        )

    assert response.status_code == 504
    assert SERVICE_TOKEN not in response.text


async def test_a_recording_past_the_limit_is_413_and_never_reaches_the_service(
    database, llm, registry
) -> None:
    """Over sixty seconds is refused here, before a container is woken.

    Both ends enforce it — the service measures the converted WAV rather than
    trusting anything sent — but the cheap refusal is the one that happens
    before the audio is uploaded to another container at all.
    """
    reached: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(str(request.url))
        return httpx.Response(200, json={"text": "should never get here"})

    http = await _client(database, _settings(), llm, registry, handler)
    async with http:
        response = await http.post(
            "/api/voice/transcribe",
            content=AUDIO,
            headers={
                "Content-Type": "audio/webm;codecs=opus",
                "X-Audio-Duration-Seconds": "60.5",
            },
        )

    assert response.status_code == 413
    assert reached == []


async def test_a_recording_inside_the_limit_is_not_refused(
    database, llm, registry
) -> None:
    """The boundary cuts where it says it does, and not a second earlier."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "salom", "duration_seconds": 55.0})

    http = await _client(database, _settings(), llm, registry, handler)
    async with http:
        response = await http.post(
            "/api/voice/transcribe",
            content=AUDIO,
            headers={
                "Content-Type": "audio/webm;codecs=opus",
                "X-Audio-Duration-Seconds": "55",
            },
        )

    assert response.status_code == 200
    assert response.json()["duration_seconds"] == 55.0
