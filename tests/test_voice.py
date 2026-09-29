"""The voice seam.

Audio is the one kind of request this service takes that is large, opaque and
expensive to forward, so the properties worth pinning are the ones that stop it
reaching a provider at all: the caller must be the operator, the payload must be
within a size the server chose, and the container must be one the provider can
actually read. Everything after that is the existing text path, untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.conftest import OPERATOR_TOKEN
from tests.conftest import test_database_url as _database_url
from ulugbek_ai.config.settings import Settings
from ulugbek_ai.core.errors import UlugbekError
from ulugbek_ai.voice.base import SpeechToText, Transcript

#: A WebM/Opus container never reaches a provider in these tests, so the bytes
#: only have to be bytes. The leading magic number is the real one so that a
#: future sniffing check does not silently start rejecting the fixture.
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 2048


@dataclass
class FakeSpeechToText(SpeechToText):
    """Records what it was asked, answers what the test wants."""

    text: str = "salom, loyihalarimni ko'rsat"
    duration: float | None = 3.5
    raises: Exception | None = None
    calls: list[tuple[int, str, str]] = None  # type: ignore[assignment]

    #: Not a dataclass field: which containers a provider accepts is a
    #: property of the provider, and the router asks it rather than assuming.
    supported_containers = frozenset({"audio/webm", "audio/ogg"})

    def __post_init__(self) -> None:
        self.calls = []

    async def transcribe(
        self, audio: bytes, *, content_type: str, language: str
    ) -> Transcript:
        self.calls.append((len(audio), content_type, language))
        if self.raises is not None:
            raise self.raises
        return Transcript(
            text=self.text, language=language, duration_seconds=self.duration
        )


@pytest.fixture
def stt() -> FakeSpeechToText:
    return FakeSpeechToText()


@pytest.fixture
def voice_settings() -> Settings:
    """Settings with speech configured, isolated from any .env on the machine."""
    return Settings(
        _env_file=None,
        environment="test",
        auth_token=OPERATOR_TOKEN,
        database_url=_database_url(),
        stt_provider="google",
        stt_api_key="test-stt-key-not-a-real-credential",
        stt_max_bytes=4096,
        stt_max_seconds=60,
    )


def _voice_app(database, voice_settings: Settings, llm, registry, stt=None):
    """The real application, with speech configured and the provider faked.

    Built here rather than in ``conftest`` so the shared fixtures every other
    test depends on keep working exactly as they did.
    """
    from ulugbek_ai.api.deps import database_dependency, session_dependency
    from ulugbek_ai.main import create_app

    app = create_app(voice_settings)

    async def override_session():
        async with database.session() as session:
            yield session

    app.dependency_overrides[session_dependency] = override_session
    app.dependency_overrides[database_dependency] = lambda: database
    app.state.llm = llm
    app.state.registry = registry
    if stt is not None:
        app.state.stt = stt
    return app


@pytest_asyncio.fixture
async def voice_client(
    database, voice_settings: Settings, llm, registry, stt: FakeSpeechToText
):
    """Authenticated, with speech configured and the provider faked."""
    app = _voice_app(database, voice_settings, llm, registry, stt)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"},
    ) as http:
        yield http


@pytest_asyncio.fixture
async def voice_anonymous_client(
    database, voice_settings: Settings, llm, registry
):
    """Speech configured, no credential — for what health may say."""
    app = _voice_app(database, voice_settings, llm, registry)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


# --------------------------------------------------------------------------- #
# Nobody unauthenticated gets near a provider
# --------------------------------------------------------------------------- #
async def test_transcribe_refuses_an_anonymous_caller(
    anonymous_client: AsyncClient,
) -> None:
    response = await anonymous_client.post(
        "/api/voice/transcribe", content=WEBM, headers={"Content-Type": "audio/webm"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


async def test_speak_refuses_an_anonymous_caller(
    anonymous_client: AsyncClient,
) -> None:
    response = await anonymous_client.post(
        "/api/voice/speak", json={"text": "salom"}
    )

    assert response.status_code == 401


async def test_transcribe_refuses_a_wrong_token(anonymous_client: AsyncClient) -> None:
    response = await anonymous_client.post(
        "/api/voice/transcribe",
        content=WEBM,
        headers={
            "Content-Type": "audio/webm",
            "Authorization": "Bearer wrong-token-of-a-plausible-length-0123456789",
        },
    )

    assert response.status_code == 401


async def test_an_anonymous_caller_is_refused_before_the_provider_is_consulted(
    anonymous_client: AsyncClient,
) -> None:
    """401, not 503.

    The guard is attached to the router, so it runs before the endpoint's own
    dependencies. If it were listed in the signature instead, an unauthenticated
    caller would resolve the speech dependency first and be told which
    credential the server is missing.
    """
    response = await anonymous_client.post(
        "/api/voice/transcribe", content=WEBM, headers={"Content-Type": "audio/webm"}
    )

    assert response.status_code == 401
    assert "STT_API_KEY" not in response.text


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
async def test_transcribe_says_which_variable_is_missing(
    client: AsyncClient,
) -> None:
    """The default fixture configures no speech provider."""
    response = await client.post(
        "/api/voice/transcribe", content=WEBM, headers={"Content-Type": "audio/webm"}
    )

    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "configuration_error"
    assert "STT_API_KEY" in body["message"]


async def test_speak_reports_that_the_console_synthesises_locally(
    client: AsyncClient,
) -> None:
    """``TTS_PROVIDER=browser`` is the default, and it is not a server provider.

    The endpoint exists so the seam is real and protected; it says plainly that
    no server-side voice is configured rather than returning silence.
    """
    response = await client.post("/api/voice/speak", json={"text": "salom"})

    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "configuration_error"
    assert "TTS_PROVIDER" in body["message"]


# --------------------------------------------------------------------------- #
# What reaches the provider, and what does not
# --------------------------------------------------------------------------- #
async def test_audio_larger_than_the_limit_never_reaches_the_provider(
    voice_client: AsyncClient, stt: FakeSpeechToText
) -> None:
    response = await voice_client.post(
        "/api/voice/transcribe",
        content=b"\x1a\x45\xdf\xa3" + b"\x00" * 8192,  # limit is 4096
        headers={"Content-Type": "audio/webm"},
    )

    assert response.status_code == 413
    assert stt.calls == []


async def test_a_container_the_provider_cannot_read_is_refused(
    voice_client: AsyncClient, stt: FakeSpeechToText
) -> None:
    """Safari records audio/mp4, which the Google adapter cannot decode.

    Refusing it here, by name, beats forwarding it and relaying a provider
    error that says nothing about the browser.
    """
    response = await voice_client.post(
        "/api/voice/transcribe",
        content=WEBM,
        headers={"Content-Type": "audio/mp4"},
    )

    assert response.status_code == 415
    assert "audio/mp4" in response.json()["error"]["message"]
    assert stt.calls == []


async def test_audio_longer_than_the_limit_never_reaches_the_provider(
    voice_client: AsyncClient, stt: FakeSpeechToText
) -> None:
    """The recorder reports how long it recorded; over the cap, stop early.

    This saves a provider call for an honest client. It is not the real guard —
    the byte limit is, because a header can say anything.
    """
    response = await voice_client.post(
        "/api/voice/transcribe",
        content=WEBM,
        headers={"Content-Type": "audio/webm", "X-Audio-Duration-Seconds": "600"},
    )

    assert response.status_code == 413
    assert stt.calls == []


async def test_an_empty_body_is_refused(
    voice_client: AsyncClient, stt: FakeSpeechToText
) -> None:
    response = await voice_client.post(
        "/api/voice/transcribe", content=b"", headers={"Content-Type": "audio/webm"}
    )

    assert response.status_code == 422
    assert stt.calls == []


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #
async def test_a_transcript_comes_back_in_the_shape_the_console_expects(
    voice_client: AsyncClient, stt: FakeSpeechToText
) -> None:
    response = await voice_client.post(
        "/api/voice/transcribe",
        content=WEBM,
        headers={"Content-Type": "audio/webm", "X-Audio-Duration-Seconds": "3.5"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "text": "salom, loyihalarimni ko'rsat",
        "language": "uz",
        "duration_seconds": 3.5,
    }
    assert stt.calls == [(len(WEBM), "audio/webm", "uz-UZ")]


async def test_a_codec_parameter_does_not_confuse_the_container_check(
    voice_client: AsyncClient, stt: FakeSpeechToText
) -> None:
    """MediaRecorder sends ``audio/webm;codecs=opus``, not ``audio/webm``."""
    response = await voice_client.post(
        "/api/voice/transcribe",
        content=WEBM,
        headers={"Content-Type": "audio/webm;codecs=opus"},
    )

    assert response.status_code == 200
    assert stt.calls[0][1] == "audio/webm"


# --------------------------------------------------------------------------- #
# A failing provider is a failure, not a leak
# --------------------------------------------------------------------------- #
async def test_a_provider_failure_answers_safely(
    voice_client: AsyncClient, stt: FakeSpeechToText
) -> None:
    from ulugbek_ai.core.errors import SpeechError

    stt.raises = SpeechError("The speech provider rejected the request.")

    response = await voice_client.post(
        "/api/voice/transcribe", content=WEBM, headers={"Content-Type": "audio/webm"}
    )

    assert response.status_code == 502
    body = response.json()["error"]
    assert body["code"] == "speech_error"
    assert "test-stt-key" not in response.text


async def test_an_unexpected_provider_crash_does_not_reach_the_operator(
    voice_client: AsyncClient, stt: FakeSpeechToText
) -> None:
    """An adapter that raises something unforeseen still fails safely.

    Whatever the exception carried — and an HTTP library will happily put a
    full URL, key and all, into one — none of it reaches the console.
    """
    stt.raises = RuntimeError("boom: key=test-stt-key-not-a-real-credential")

    response = await voice_client.post(
        "/api/voice/transcribe", content=WEBM, headers={"Content-Type": "audio/webm"}
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "speech_error"
    assert "test-stt-key" not in response.text
    assert "boom" not in response.text


async def test_the_speech_key_is_not_written_to_the_log(
    voice_client: AsyncClient,
    stt: FakeSpeechToText,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The exception carries the key, the way a naive adapter would leak it.
    stt.raises = RuntimeError(
        "GET https://speech.googleapis.com/v1/speech:recognize"
        "?key=test-stt-key-not-a-real-credential failed"
    )

    with caplog.at_level(logging.DEBUG):
        await voice_client.post(
            "/api/voice/transcribe",
            content=WEBM,
            headers={"Content-Type": "audio/webm"},
        )

    assert "test-stt-key-not-a-real-credential" not in caplog.text


# --------------------------------------------------------------------------- #
# Audio is not kept
# --------------------------------------------------------------------------- #
def test_the_voice_routes_cannot_reach_the_database() -> None:
    """Nothing can be stored by code that has no way to store it.

    A test that asserts "no rows were written" only covers the tables it thinks
    to look at. This checks the stronger property: neither endpoint takes a
    database session or the database itself, so audio cannot be persisted by
    either of them however they are changed later.
    """
    import inspect

    from ulugbek_ai.api.deps import (
        database_dependency,
        session_dependency,
    )
    from ulugbek_ai.api.routes import voice

    forbidden = {session_dependency, database_dependency}
    for route in voice.router.routes:
        endpoint = route.endpoint  # type: ignore[attr-defined]
        for parameter in inspect.signature(endpoint).parameters.values():
            annotation = parameter.annotation
            for dependency in getattr(annotation, "__metadata__", ()):
                assert getattr(dependency, "dependency", None) not in forbidden, (
                    f"{endpoint.__name__} can reach the database"
                )


# --------------------------------------------------------------------------- #
# Health reports the state without reporting the value
# --------------------------------------------------------------------------- #
async def test_health_reports_speech_configuration_without_revealing_it(
    anonymous_client: AsyncClient,
) -> None:
    body = (await anonymous_client.get("/api/health")).json()

    assert body["stt"] == {"configured": False, "usable": False}
    assert body["tts"] == {"configured": False, "usable": False}


async def test_health_reports_speech_as_usable_once_configured(
    voice_anonymous_client: AsyncClient,
) -> None:
    response = await voice_anonymous_client.get("/api/health")
    body = response.json()

    assert response.status_code == 200
    assert body["stt"] == {"configured": True, "usable": True}
    assert "test-stt-key-not-a-real-credential" not in response.text


# --------------------------------------------------------------------------- #
# The seam itself
# --------------------------------------------------------------------------- #
def test_a_transcript_carries_only_what_the_console_needs() -> None:
    transcript = Transcript(text="salom", language="uz-UZ", duration_seconds=1.0)

    assert transcript.text == "salom"
    # The provider's raw response is not part of the contract, so an adapter
    # cannot leak request ids, quotas or keys through it.
    assert set(transcript.__dataclass_fields__) == {
        "text",
        "language",
        "duration_seconds",
    }


def test_speech_errors_are_domain_errors() -> None:
    from ulugbek_ai.core.errors import SpeechError

    assert issubclass(SpeechError, UlugbekError)
    assert SpeechError.http_status == 502
