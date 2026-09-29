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

import httpx
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
        audio_ctx=None,
        audio_ctx_headroom=2.0,
        audio_ctx_floor=256,
        vad=False,
        vad_model="/nonexistent/vad.bin",
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
        self.windows: list[int] = []
        self.budgets: list[float | None] = []

    async def transcribe(
        self, wav: Path, *, audio_ctx: int = 0, timeout: float | None = None
    ) -> str:
        self.calls += 1
        self.windows.append(audio_ctx)
        self.budgets.append(timeout)
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


# --------------------------------------------------------------------------- #
# The window is sized to the recording
# --------------------------------------------------------------------------- #
def test_every_window_reaches_the_end_of_its_recording() -> None:
    """The property the whole design rests on.

    Audio past the encoder window is not transcribed badly — it is not
    transcribed at all, and the request still succeeds. So for anything inside
    whisper's own 30-second chunk, the window must cover the audio.
    """
    for duration in (0.5, 1, 2, 5, 9, 11, 14, 15, 18, 22, 25, 27, 29, 29.9):
        ctx = service.encoder_context_for(duration, headroom=2.0, floor=256)
        assert service.window_seconds(ctx) >= duration, (
            f"{duration}s got a {service.window_seconds(ctx)}s window"
        )


def test_longer_than_a_chunk_falls_back_to_whisper_s_own_handling() -> None:
    """Past 30 seconds whisper loops over chunks, exactly as it does at 0.

    Shortening the window there would break the seek it uses to advance, which
    is what a fixed 768 did to the 22-second sample.
    """
    for duration in (28.1, 30, 45, 60, 120):
        assert service.encoder_context_for(duration, headroom=2.0, floor=256) == 0


def test_a_short_command_gets_a_much_smaller_window_than_a_fixed_768() -> None:
    # The common case: a spoken command of a few seconds.
    assert service.encoder_context_for(2.0, headroom=2.0, floor=256) == 256
    assert service.encoder_context_for(5.0, headroom=2.0, floor=256) == 350
    # And a long one gets more than 768, which is the case 768 got wrong.
    assert service.encoder_context_for(22.0, headroom=2.0, floor=256) == 1200


def test_the_floor_keeps_very_short_clips_out_of_the_repeating_range() -> None:
    assert service.encoder_context_for(0.2, headroom=0.0, floor=256) == 256
    assert service.encoder_context_for(0.2, headroom=0.0, floor=64) == 64


async def test_the_window_reaches_the_model_and_is_reported(
    client, engine: FakeWhisper, monkeypatch
) -> None:
    monkeypatch.setattr(
        service, "convert_to_wav", lambda source, dest, **k: write_wav(dest, 3.0)
    )

    response = post(client, b"audio")

    assert response.status_code == 200
    # 3s + 2s headroom = 250 positions, below the floor, so the floor.
    assert engine.windows == [256]
    assert response.json()["audio_ctx"] == 256


async def test_a_pinned_window_overrides_the_measurement(
    engine: FakeWhisper, monkeypatch
) -> None:
    monkeypatch.setattr(
        service, "convert_to_wav", lambda source, dest, **k: write_wav(dest, 3.0)
    )
    app = service.create_app(
        make_config(audio_ctx=768, max_seconds=15.0),
        whisper=engine,  # type: ignore[arg-type]
    )
    with TestClient(app) as http:
        response = post(http, b"audio")

    assert response.status_code == 200
    assert engine.windows == [768]


def test_auto_is_what_an_unset_variable_means(monkeypatch) -> None:
    monkeypatch.delenv("RUBAI_AUDIO_CTX", raising=False)
    assert service.Config.from_env().audio_ctx is None
    monkeypatch.setenv("RUBAI_AUDIO_CTX", "auto")
    assert service.Config.from_env().audio_ctx is None
    monkeypatch.setenv("RUBAI_AUDIO_CTX", "768")
    assert service.Config.from_env().audio_ctx == 768


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


def test_the_per_recording_default_never_trips_that_guard() -> None:
    """It cannot lose the end of a recording, so there is nothing to refuse."""
    app = service.create_app(
        make_config(audio_ctx=None, max_seconds=60.0),
        whisper=FakeWhisper(),  # type: ignore[arg-type]
    )

    assert app is not None


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


async def test_the_window_is_actually_put_on_the_wire() -> None:
    """What the route decided has to arrive at whisper.

    Deleting the two lines that put `audio_ctx` in the form failed no test
    until this one existed: the fake whisper in every other test records the
    argument it was called with, which says nothing about what was sent.
    """
    sent: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200, json={"text": "salom"})

    wav = Path(tempfile.mkdtemp(prefix="rubai-test-")) / "a.wav"
    write_wav(wav, 1.0)
    try:
        engine = service.WhisperServer(
            make_config(),
            client=httpx.AsyncClient(
                base_url="http://whisper", transport=httpx.MockTransport(handler)
            ),
        )
        await engine.transcribe(wav, audio_ctx=650)
        assert b'name="audio_ctx"' in sent[0]
        assert b"650" in sent[0]

        # 0 means whisper's own full window; sending the number would be the
        # same thing said twice, and its default path is the tested one.
        sent.clear()
        await engine.transcribe(wav, audio_ctx=0)
        assert b'name="audio_ctx"' not in sent[0]
    finally:
        shutil.rmtree(wav.parent, ignore_errors=True)


async def test_voice_detection_is_requested_only_when_it_is_switched_on() -> None:
    sent: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200, json={"text": "salom"})

    wav = Path(tempfile.mkdtemp(prefix="rubai-test-")) / "a.wav"
    write_wav(wav, 1.0)
    try:
        for vad, expected in ((False, False), (True, True)):
            sent.clear()
            engine = service.WhisperServer(
                make_config(vad=vad),
                client=httpx.AsyncClient(
                    base_url="http://whisper",
                    transport=httpx.MockTransport(handler),
                ),
            )
            await engine.transcribe(wav, audio_ctx=256)
            assert (b'name="vad"' in sent[0]) is expected
    finally:
        shutil.rmtree(wav.parent, ignore_errors=True)


def test_voice_detection_is_off_unless_asked_for(monkeypatch) -> None:
    monkeypatch.delenv("RUBAI_VAD", raising=False)
    assert service.Config.from_env().vad is False
    monkeypatch.setenv("RUBAI_VAD", "on")
    assert service.Config.from_env().vad is True


def test_the_window_is_not_a_startup_flag() -> None:
    """It rides on each request instead.

    whisper-server reads audio_ctx from the form as well as the command line
    (server.cpp), which is what lets one warm model serve a window sized to
    each recording rather than one number for the life of the process.
    """
    for configured in (None, 768):
        argv = service.WhisperServer(make_config(audio_ctx=configured)).argv()
        assert "--audio-ctx" not in argv


def test_duration_is_read_from_the_file_rather_than_a_second_process() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="rubai-test-"))
    try:
        wav = workspace / "a.wav"
        write_wav(wav, 2.5)
        assert service.wav_duration_seconds(wav) == pytest.approx(2.5, abs=0.01)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


# --------------------------------------------------------------------------- #
# The timeout budget
#
# The API in front of this service waits STT_TIMEOUT_SECONDS for an answer. If
# this service can take longer than that, the caller gets a 504 while the model
# keeps spending CPU on a transcript nobody is waiting for any more. So the
# whole answer -- queue wait and inference together -- is bounded here, below
# what the API will wait.
# --------------------------------------------------------------------------- #
def test_the_internal_budget_is_capped_however_it_is_configured(monkeypatch) -> None:
    monkeypatch.setenv("STT_SERVICE_TOKEN", TOKEN)

    monkeypatch.delenv("RUBAI_REQUEST_TIMEOUT_SECONDS", raising=False)
    assert service.Config.from_env().request_timeout == 120.0

    monkeypatch.setenv("RUBAI_REQUEST_TIMEOUT_SECONDS", "999")
    assert service.Config.from_env().request_timeout == 120.0

    # Below the ceiling it is taken as written: the cap is a maximum, not a
    # fixed value.
    monkeypatch.setenv("RUBAI_REQUEST_TIMEOUT_SECONDS", "45")
    assert service.Config.from_env().request_timeout == 45.0


def test_the_queue_and_the_model_share_one_budget(engine: FakeWhisper, monkeypatch) -> None:
    """Waiting in the queue spends the same budget the inference does.

    Two requests, one slot: without a shared deadline the second one waits its
    full budget for the slot and then gets a fresh full budget for the model,
    so the service can answer in twice what it promised.
    """
    monkeypatch.setattr(
        service, "convert_to_wav", lambda source, dest, **k: write_wav(dest, 1.0)
    )
    engine.delay = 0.2
    app = service.create_app(  # type: ignore[arg-type]
        make_config(concurrency=1, request_timeout=1.0), whisper=engine
    )

    with TestClient(app) as http:
        import threading

        def call() -> None:
            post(http, b"audio")

        threads = [threading.Thread(target=call) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert len(engine.budgets) == 2
    # Handed a budget at all, before asking whether it was the right one.
    assert all(budget is not None for budget in engine.budgets), (
        "the route computed a remaining budget and did not pass it on"
    )
    # The one that queued behind the other cannot have been given the whole
    # budget: it had already spent some of it waiting.
    assert min(engine.budgets) < 1.0 - 0.15
    # And nobody is given more than the budget.
    assert max(engine.budgets) <= 1.0


async def test_the_budget_is_actually_put_on_the_wire() -> None:
    """A budget the route computed and did not send is not a budget.

    The fake whisper in the tests above records the argument it was handed,
    which says nothing about what reached httpx.
    """
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions.get("timeout"))
        return httpx.Response(200, json={"text": "salom"})

    wav = Path(tempfile.mkdtemp(prefix="rubai-test-")) / "a.wav"
    write_wav(wav, 1.0)
    try:
        engine = service.WhisperServer(
            make_config(request_timeout=90.0),
            client=httpx.AsyncClient(
                base_url="http://whisper",
                timeout=90.0,
                transport=httpx.MockTransport(handler),
            ),
        )
        await engine.transcribe(wav, audio_ctx=256, timeout=7.5)
        assert seen[0]["read"] == pytest.approx(7.5)

        # Without one it falls back to the client's own timeout, which is the
        # path the warm-up call takes.
        seen.clear()
        await engine.transcribe(wav, audio_ctx=256)
        assert seen[0]["read"] == pytest.approx(90.0)
    finally:
        shutil.rmtree(wav.parent, ignore_errors=True)


def test_a_model_that_runs_out_of_time_is_a_gateway_timeout(
    client, engine: FakeWhisper
) -> None:
    """504, not 502.

    The API in front reads the status to decide what to tell the operator, and
    "the model took too long" and "the model broke" are different things to be
    told.
    """
    engine.raises = httpx.ReadTimeout("timed out")

    response = post(client, b"audio")

    assert response.status_code == 504
    assert "127.0.0.1" not in response.text
    assert TOKEN not in response.text


def test_a_timed_out_inference_leaves_nothing_behind(client, engine: FakeWhisper) -> None:
    """No file on disk, and no slot held for a request that is already over.

    A semaphore released only on the happy path wedges the service after the
    first timeout: every later request queues behind a slot nobody holds.
    """
    before = set(Path(tempfile.gettempdir()).glob("rubai-*"))

    engine.raises = httpx.ReadTimeout("timed out")
    assert post(client, b"audio").status_code == 504

    assert set(Path(tempfile.gettempdir()).glob("rubai-*")) == before

    # The slot has to have come back, or this one never answers.
    engine.raises = None
    assert post(client, b"audio").status_code == 200
