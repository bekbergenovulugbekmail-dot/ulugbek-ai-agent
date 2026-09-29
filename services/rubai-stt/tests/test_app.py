"""The guard in front of the model.

This service is the only part of the system reachable with a 500 MB model
behind it, so what is pinned here is what stops someone else spending that
model's CPU, and what stops a recording surviving the request that carried it.

The model itself is never loaded: these run in seconds, on a machine with no
weights and no ffmpeg, which is exactly what makes them worth running on every
push.
"""

from __future__ import annotations

import asyncio
import shutil
import struct
import sys
import tempfile
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as service  # noqa: E402

TOKEN = "test-service-token-not-a-real-credential"


def make_config(**overrides) -> service.Config:
    defaults = dict(
        port=8080,
        whisper_bin="/nonexistent",
        whisper_port=8081,
        model_path="/nonexistent/model.bin",
        threads=1,
        language="uz",
        token=TOKEN,
        allow_anonymous=False,
        max_bytes=4096,
        max_seconds=5.0,
        concurrency=1,
        request_timeout=5.0,
        startup_timeout=5.0,
        audio_ctx=0,
    )
    defaults.update(overrides)
    return service.Config(**defaults)


class FakeWhisper:
    """The model, without the model."""

    def __init__(self, text: str = "salom dunyo", ready: bool = True) -> None:
        self.ready = ready
        self.text = text
        self.calls = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self.delay = 0.0
        self.raises: Exception | None = None

    async def transcribe(self, wav: Path) -> str:
        self.calls += 1
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.raises is not None:
                raise self.raises
            return self.text
        finally:
            self.concurrent -= 1

    async def stop(self) -> None:
        return None


def write_wav(path: Path, seconds: float) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(service.SAMPLE_RATE)
        handle.writeframes(struct.pack("<h", 0) * int(service.SAMPLE_RATE * seconds))


@pytest.fixture
def engine() -> FakeWhisper:
    return FakeWhisper()


@pytest.fixture
def client(engine: FakeWhisper, monkeypatch: pytest.MonkeyPatch):
    """The real app, with the model and ffmpeg replaced by doubles."""

    def fake_convert(source: Path, destination: Path, **_: object) -> None:
        # Stands in for ffmpeg: a fixed one second of silence, unless the test
        # asked for something undecodable.
        if source.read_bytes().startswith(b"BROKEN"):
            raise service.AudioError("That recording could not be decoded.")
        write_wav(destination, 1.0)

    monkeypatch.setattr(service, "convert_to_wav", fake_convert)
    app = service.create_app(make_config(), whisper=engine)  # type: ignore[arg-type]
    with TestClient(app) as http:
        yield http


def post(http, body: bytes, *, token: str | None = TOKEN, filename: str = "a.webm"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return http.post(
        "/inference",
        files={"file": (filename, body, "audio/webm")},
        headers=headers,
    )


# --------------------------------------------------------------------------- #
# Nobody without the token reaches the model
# --------------------------------------------------------------------------- #
def test_an_anonymous_caller_is_refused(client) -> None:
    response = post(client, b"audio", token=None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_a_wrong_token_is_refused(client, engine: FakeWhisper) -> None:
    response = post(client, b"audio", token="wrong-token-of-a-plausible-length")

    assert response.status_code == 401
    assert engine.calls == 0


def test_the_service_refuses_to_start_without_a_token() -> None:
    """Starting open is worse than not starting.

    This holds a model anyone can spend CPU on. A deployment that forgets the
    token should fail at boot, where it is noticed, not serve quietly.
    """
    with pytest.raises(RuntimeError, match="STT_SERVICE_TOKEN"):
        service.create_app(make_config(token=None), whisper=FakeWhisper())  # type: ignore[arg-type]


def test_a_private_deployment_may_opt_out_of_the_token() -> None:
    app = service.create_app(
        make_config(token=None, allow_anonymous=True),
        whisper=FakeWhisper(),  # type: ignore[arg-type]
    )

    assert app is not None


# --------------------------------------------------------------------------- #
# Health says what it means
# --------------------------------------------------------------------------- #
def test_health_is_503_until_the_model_answers(engine: FakeWhisper) -> None:
    engine.ready = False
    app = service.create_app(make_config(), whisper=engine)  # type: ignore[arg-type]
    with TestClient(app) as http:
        response = http.get("/health")

    # The platform's health check is the only thing between a half-started
    # container and live traffic.
    assert response.status_code == 503
    assert response.json()["model_loaded"] is False


def test_health_is_200_once_it_does(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_inference_waits_for_the_model(engine: FakeWhisper, monkeypatch) -> None:
    engine.ready = False
    monkeypatch.setattr(service, "convert_to_wav", lambda *a, **k: None)
    app = service.create_app(make_config(), whisper=engine)  # type: ignore[arg-type]
    with TestClient(app) as http:
        response = post(http, b"audio")

    assert response.status_code == 503
    assert engine.calls == 0


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #
def test_audio_over_the_byte_limit_is_refused(client, engine: FakeWhisper) -> None:
    response = post(client, b"x" * 8192)  # limit is 4096

    assert response.status_code == 413
    assert engine.calls == 0


def test_an_empty_upload_is_refused(client, engine: FakeWhisper) -> None:
    response = post(client, b"")

    assert response.status_code == 422
    assert engine.calls == 0


def test_audio_over_the_duration_limit_is_refused(
    client, engine: FakeWhisper, monkeypatch
) -> None:
    monkeypatch.setattr(
        service, "convert_to_wav", lambda source, dest, **k: write_wav(dest, 30.0)
    )

    response = post(client, b"audio")  # limit is 5 seconds

    assert response.status_code == 413
    assert "longer than" in response.json()["error"]
    assert engine.calls == 0


def test_undecodable_audio_is_refused_before_the_model(
    client, engine: FakeWhisper
) -> None:
    response = post(client, b"BROKEN not really audio")

    assert response.status_code == 415
    assert engine.calls == 0


# --------------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------------- #
def test_a_recording_comes_back_as_text(client, engine: FakeWhisper) -> None:
    response = post(client, b"audio bytes")

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "salom dunyo"
    assert body["language"] == "uz"
    assert body["duration_seconds"] == 1.0
    assert isinstance(body["latency_ms"], int)
    assert engine.calls == 1


def test_a_hostile_filename_is_simply_ignored(client, engine: FakeWhisper) -> None:
    """Names come from this process; the uploaded one is never used.

    Not sanitised — unused. A name that is never put on a path or a command
    line cannot escape either.
    """
    response = post(client, b"audio", filename="../../../etc/passwd;rm -rf /")

    assert response.status_code == 200
    assert engine.calls == 1


# --------------------------------------------------------------------------- #
# Nothing survives the request
# --------------------------------------------------------------------------- #
def test_every_temporary_file_is_gone_afterwards(client) -> None:
    before = set(Path(tempfile.gettempdir()).glob("rubai-*"))

    post(client, b"audio bytes")
    post(client, b"BROKEN")  # the error path has to clean up too
    post(client, b"x" * 8192)

    assert set(Path(tempfile.gettempdir()).glob("rubai-*")) == before


def test_the_model_is_asked_one_thing_at_a_time(
    engine: FakeWhisper, monkeypatch
) -> None:
    """Whisper medium holds its working set in memory for the whole inference.

    Two at once doubles that, on a container sized for one, and the second
    request is no faster for having started earlier.
    """
    monkeypatch.setattr(
        service, "convert_to_wav", lambda source, dest, **k: write_wav(dest, 1.0)
    )
    engine.delay = 0.05
    app = service.create_app(make_config(concurrency=1), whisper=engine)  # type: ignore[arg-type]

    with TestClient(app) as http:
        import threading

        results: list[int] = []

        def call() -> None:
            results.append(post(http, b"audio").status_code)

        threads = [threading.Thread(target=call) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert results == [200, 200, 200, 200]
    assert engine.max_concurrent == 1


# --------------------------------------------------------------------------- #
# Failures stay inside
# --------------------------------------------------------------------------- #
def test_a_model_failure_answers_without_detail(
    client, engine: FakeWhisper
) -> None:
    engine.raises = RuntimeError(
        "connect to http://127.0.0.1:8081 failed, token=test-service-token"
    )

    response = post(client, b"audio")

    assert response.status_code == 502
    assert "127.0.0.1" not in response.text
    assert "test-service-token" not in response.text


# --------------------------------------------------------------------------- #
# The conversion command
# --------------------------------------------------------------------------- #
def test_the_converter_never_goes_through_a_shell(monkeypatch) -> None:
    """ffmpeg is handed an argument list, from paths this process generated."""
    seen: dict[str, object] = {}

    class Result:
        returncode = 0
        stderr = b""

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        Path(argv[-1]).write_bytes(b"RIFF")
        return Result()

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    workspace = Path(tempfile.mkdtemp(prefix="rubai-test-"))
    try:
        service.convert_to_wav(workspace / "in", workspace / "out.wav")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    argv = seen["argv"]
    assert isinstance(argv, list)
    assert seen["kwargs"].get("shell") in (None, False)
    # A crafted container must not make ffmpeg fetch a second resource.
    assert "-protocol_whitelist" in argv
    assert argv[argv.index("-protocol_whitelist") + 1] == "file"
    assert "-nostdin" in argv


def test_every_flag_is_passed_as_a_flag_and_every_option_with_a_value() -> None:
    """whisper-server answers a flag given a value by printing its usage.

    It then exits 0, which from the outside is a clean shutdown — the container
    reported "the model server stopped" and nothing said why. One deploy to
    find, so the shape of the command line is pinned here.
    """
    engine = service.WhisperServer(make_config(whisper_bin="/bin/whisper-server"))
    argv = engine.argv()

    assert argv[0] == "/bin/whisper-server"
    for index, item in enumerate(argv[1:], start=1):
        if item in service.WhisperServer.OPTIONS:
            value = argv[index + 1] if index + 1 < len(argv) else None
            assert value is not None and not value.startswith("--"), item
        elif item in service.WhisperServer.FLAGS:
            following = argv[index + 1] if index + 1 < len(argv) else None
            assert following is None or following.startswith("--"), item
        elif item.startswith("--"):
            raise AssertionError(f"{item} is neither a declared option nor a flag")

    # The language is stated rather than detected.
    assert argv[argv.index("--language") + 1] == "uz"
    # Off by default: shortening the encoder context is the only lever that
    # makes a short command cheaper, and its cost on Uzbek is not yet measured.
    assert "--audio-ctx" not in argv
    # The model server is never reachable from outside the container.
    assert argv[argv.index("--host") + 1] == "127.0.0.1"


def test_a_context_that_cannot_hear_the_whole_recording_refuses_to_start() -> None:
    """Measured: at 768 a 22-second clip came back different from the baseline.

    768 positions cover 15.4 seconds. A service that accepts 60 and hears 15
    does not fail — it answers with a plausible transcript that is missing the
    end, which is worse than any error.
    """
    with pytest.raises(RuntimeError, match="RUBAI_AUDIO_CTX"):
        service.create_app(
            make_config(audio_ctx=768, max_seconds=60.0),
            whisper=FakeWhisper(),  # type: ignore[arg-type]
        )


def test_a_context_that_covers_the_limit_is_accepted() -> None:
    app = service.create_app(
        make_config(audio_ctx=768, max_seconds=15.0),
        whisper=FakeWhisper(),  # type: ignore[arg-type]
    )

    assert app is not None


def test_the_window_is_the_ratio_whisper_actually_uses() -> None:
    # 1500 positions over 30 seconds.
    assert service.window_seconds(0) == 30.0
    assert service.window_seconds(1500) == 30.0
    assert service.window_seconds(768) == pytest.approx(15.36)
    assert service.window_seconds(512) == pytest.approx(10.24)


def test_the_encoder_context_reaches_the_model_when_it_is_set() -> None:
    engine = service.WhisperServer(make_config(audio_ctx=768))
    argv = engine.argv()

    assert argv[argv.index("--audio-ctx") + 1] == "768"


def test_duration_is_read_from_the_file_rather_than_a_second_process() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="rubai-test-"))
    try:
        wav = workspace / "a.wav"
        write_wav(wav, 2.5)
        assert service.wav_duration_seconds(wav) == pytest.approx(2.5, abs=0.01)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
