"""The Uzbek speech service.

A thin, careful shell around `whisper-server`. The shell exists because
`whisper-server` alone cannot be put on a network: it has no authentication, no
request limit, and it reads only WAV — while a browser records Opus in WebM.

The division of labour is deliberate:

* `whisper-server` listens on loopback only and never sees the internet. It
  holds the model in memory so a request costs an inference and not a 500 MB
  load.
* This process is the only thing bound to the public port. It checks the
  credential, enforces the limits, converts the audio, and deletes every byte
  it wrote before it answers.

Nothing is stored. Audio arrives as a request body, becomes a temporary WAV,
and is unlinked in a `finally` — there is no path here that keeps it.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Final

import httpx
from fastapi import FastAPI, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("rubai-stt")

#: 16 kHz, mono, 16-bit PCM — what Whisper wants, and what makes a WAV's
#: duration a division rather than a second subprocess.
SAMPLE_RATE: Final[int] = 16_000
BYTES_PER_SECOND: Final[int] = SAMPLE_RATE * 2

#: Whisper's encoder has 1500 positions covering a 30-second window, so each
#: position is 1/50th of a second. Shortening the context shortens the window
#: by exactly that ratio — and audio past the end of it is not transcribed
#: badly, it is not transcribed at all.
ENCODER_POSITIONS_PER_SECOND: Final[int] = 50
ENCODER_POSITIONS_MAX: Final[int] = 1500

#: The ceiling on everything one request may take here, queue wait included.
#: The API in front waits STT_TIMEOUT_SECONDS=150 for an answer, so a budget
#: above this is time the caller has already stopped waiting for -- it answers
#: 504 and this service carries on spending a CPU on a transcript nobody will
#: read. 120 leaves thirty seconds of slack for the network and the API's own
#: work either side.
MAX_REQUEST_BUDGET_SECONDS: Final[float] = 120.0


def window_seconds(audio_ctx: int) -> float:
    """How much audio an encoder context of *audio_ctx* positions can hear."""
    return 30.0 if audio_ctx <= 0 else audio_ctx / ENCODER_POSITIONS_PER_SECOND


def encoder_context_for(
    duration_seconds: float, *, headroom: float, floor: int
) -> int:
    """An encoder window sized to this recording. 0 means the full 1500.

    The measured saving from a shorter window was about half the wall time, and
    its measured cost was that audio past the window is not heard at all. Both
    facts point at the same answer: make the window fit the audio instead of
    picking one number for every recording.

    A two-second command then gets a far smaller window than a fixed 768 would
    have given it, and a twenty-two second one gets a window wide enough to
    reach its end — which is the case a fixed 768 got wrong.

    `headroom` is slack past the audio, because a window that ends exactly at
    the last syllable is a window that may clip it. Anything needing the full
    1500 gets 0, which lets whisper use its own default path rather than a
    number that happens to equal it.
    """
    if duration_seconds <= 0:
        return 0
    needed = math.ceil((duration_seconds + headroom) * ENCODER_POSITIONS_PER_SECOND)
    if needed >= ENCODER_POSITIONS_MAX:
        return 0
    return max(floor, needed)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _audio_ctx(name: str) -> int | None:
    """``auto`` (the default) means per-recording; anything else is a number."""
    raw = (os.environ.get(name) or "auto").strip().lower()
    if raw in ("", "auto"):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    port: int
    whisper_bin: str
    whisper_port: int
    model_path: str
    threads: int
    language: str
    token: str | None
    allow_anonymous: bool
    max_bytes: int
    max_seconds: float
    concurrency: int
    request_timeout: float
    startup_timeout: float
    #: ``None`` means one window per recording, sized to it. An integer pins
    #: every request to that window; 0 is whisper's full 1500.
    audio_ctx: int | None
    audio_ctx_headroom: float
    audio_ctx_floor: int
    vad: bool
    vad_model: str

    @classmethod
    def from_env(cls) -> "Config":
        token = (os.environ.get("STT_SERVICE_TOKEN") or "").strip() or None
        return cls(
            port=_int("PORT", 8080),
            whisper_bin=os.environ.get("WHISPER_BIN", "/app/bin/whisper-server"),
            # Loopback: the model server is never reachable from outside this
            # container, so the credential check cannot be walked around.
            whisper_port=_int("WHISPER_PORT", 8081),
            model_path=os.environ.get("WHISPER_MODEL", "/app/models/model.bin"),
            threads=_int("WHISPER_THREADS", os.cpu_count() or 4),
            language=os.environ.get("STT_LANGUAGE", "uz"),
            token=token,
            allow_anonymous=os.environ.get("RUBAI_ALLOW_ANONYMOUS", "").lower()
            in ("1", "true", "yes"),
            max_bytes=_int("STT_MAX_BYTES", 10 * 1024 * 1024),
            max_seconds=_float("STT_MAX_SECONDS", 60.0),
            # One at a time by default. Whisper medium holds its working set in
            # memory for the length of an inference; two at once doubles it, and
            # the second request is not faster for having started earlier.
            concurrency=_int("RUBAI_CONCURRENCY", 1),
            # Clamped, not merely defaulted: a value above the ceiling is a
            # promise this service cannot keep to the API in front of it.
            request_timeout=min(
                _float("RUBAI_REQUEST_TIMEOUT_SECONDS", MAX_REQUEST_BUDGET_SECONDS),
                MAX_REQUEST_BUDGET_SECONDS,
            ),
            startup_timeout=_float("RUBAI_STARTUP_TIMEOUT_SECONDS", 300.0),
            # "auto" sizes the window to each recording. A number pins every
            # request to it, which is only safe while STT_MAX_SECONDS fits
            # inside it — the check for that is in create_app.
            audio_ctx=_audio_ctx("RUBAI_AUDIO_CTX"),
            audio_ctx_headroom=_float("RUBAI_AUDIO_CTX_HEADROOM_SECONDS", 2.0),
            # Very small windows are where whisper starts repeating itself, so
            # even a one-second clip gets this much.
            audio_ctx_floor=_int("RUBAI_AUDIO_CTX_FLOOR", 256),
            # Off until measured. Voice activity detection drops the silence
            # before whisper decodes it, which is the other half of the same
            # waste — but a detector that mistakes quiet speech for silence
            # removes words, and that is worse than the time it saves.
            vad=(os.environ.get("RUBAI_VAD", "").lower() in ("1", "true", "on", "yes")),
            vad_model=os.environ.get("WHISPER_VAD_MODEL", "/app/models/vad.bin"),
        )


class AudioError(Exception):
    """The upload is not something this service can transcribe."""

    def __init__(self, message: str, status: int = 415) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def convert_to_wav(source: Path, destination: Path, *, ffmpeg: str = "ffmpeg") -> None:
    """Decode anything the browser recorded into 16 kHz mono PCM.

    Run as an argument list with no shell, from paths this process generated —
    the uploaded filename is never used for anything, on disk or on a command
    line. ``-protocol_whitelist file`` keeps a crafted container from making
    ffmpeg fetch a second resource of someone else's choosing.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, generated paths
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-protocol_whitelist",
            "file",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "wav",
            "-y",
            str(destination),
        ],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not destination.exists():
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise AudioError(
            "That recording could not be decoded. "
            + (detail[-1] if detail else "The container is not readable.")
        )


def wav_duration_seconds(path: Path) -> float:
    """Length of a 16 kHz mono 16-bit PCM file, from its size."""
    header = 44
    return max(0.0, (path.stat().st_size - header) / BYTES_PER_SECOND)


class WhisperServer:
    """The model process, started once and kept warm."""

    def __init__(
        self, config: Config, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._config = config
        self._process: subprocess.Popen[bytes] | None = None
        # Injectable so a test can see what actually goes on the wire. The
        # window only matters if it reaches whisper, and a double standing in
        # for this class cannot show that it did.
        self._client = client or httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{config.whisper_port}",
            timeout=config.request_timeout,
        )
        self.ready = False

    #: Options take a value; flags do not. whisper-server answers a flag given
    #: a value by printing its usage and exiting 0 — which from the outside is
    #: indistinguishable from a clean shutdown, and cost one deploy to find.
    OPTIONS = frozenset(
        {
            "--model",
            "--host",
            "--port",
            "--threads",
            "--language",
            "--audio-ctx",
            "--vad-model",
        }
    )
    FLAGS = frozenset({"--no-timestamps"})

    def argv(self) -> list[str]:
        cfg = self._config
        return [
            cfg.whisper_bin,
            "--model",
            cfg.model_path,
            "--host",
            "127.0.0.1",
            "--port",
            str(cfg.whisper_port),
            "--threads",
            str(cfg.threads),
            # Stated, not detected: Whisper's auto-detect mistakes Uzbek for
            # Arabic-script languages often enough to matter.
            "--language",
            cfg.language,
            "--no-timestamps",
        ] + (
            # The path is a startup flag; whether to *use* it is per request,
            # so one warm model serves both with and without.
            ["--vad-model", cfg.vad_model]
            if cfg.vad and Path(cfg.vad_model).exists()
            else []
        )

    def spawn(self) -> None:
        cfg = self._config
        if not Path(cfg.model_path).exists():
            raise RuntimeError(f"model file missing at {cfg.model_path}")
        logger.info("Starting whisper-server on 127.0.0.1:%d", cfg.whisper_port)
        self._process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            self.argv(),
        )


    async def wait_until_ready(self, timeout: float) -> None:
        """Poll until the model answers, not merely until the port is open."""
        deadline = time.monotonic() + timeout
        silence = Path(tempfile.gettempdir()) / "rubai-warmup.wav"
        _write_silence(silence)
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(
                    f"whisper-server exited with {self._process.returncode}"
                )
            try:
                await self.transcribe(silence)
                self.ready = True
                logger.info("Model loaded and answering")
                return
            except Exception:
                await asyncio.sleep(2)
        raise RuntimeError("whisper-server did not become ready in time")

    async def transcribe(
        self, wav: Path, *, audio_ctx: int = 0, timeout: float | None = None
    ) -> str:
        data = {
            "temperature": "0.0",
            "response_format": "json",
            "language": self._config.language,
        }
        # Per request, not per process: whisper-server reads audio_ctx from the
        # form (server.cpp), which is what lets one warm model serve a
        # different window for every recording.
        if audio_ctx > 0:
            data["audio_ctx"] = str(audio_ctx)
        if self._config.vad:
            data["vad"] = "true"
        with wav.open("rb") as handle:
            response = await self._client.post(
                "/inference",
                files={"file": ("audio.wav", handle, "audio/wav")},
                data=data,
                # What is left of the request's budget, not a fresh one. Absent
                # only for the warm-up call, which has no caller waiting.
                **({} if timeout is None else {"timeout": timeout}),
            )
        response.raise_for_status()
        payload: Any = response.json()
        if not isinstance(payload, dict) or "text" not in payload:
            raise ValueError("whisper-server returned an unexpected body")
        return str(payload["text"]).strip()

    async def stop(self) -> None:
        await self._client.aclose()
        process = self._process
        if process is None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - a wedged child
            process.kill()


def _write_silence(path: Path, seconds: float = 0.4) -> None:
    """A minimal valid WAV, written without calling anything."""
    import struct
    import wave

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(
            struct.pack("<h", 0) * int(SAMPLE_RATE * seconds)
        )


def create_app(
    config: Config | None = None, whisper: WhisperServer | None = None
) -> FastAPI:
    cfg = config or Config.from_env()
    engine = whisper if whisper is not None else WhisperServer(cfg)
    gate = asyncio.Semaphore(cfg.concurrency)

    # A *pinned* window that cannot reach the end of an accepted recording is
    # silent data loss: the request succeeds, the transcript looks plausible,
    # and the last half of what was said is simply absent. Measured: at a fixed
    # 768 the same sentence twice over came back a different number of times.
    # The default sizes the window per recording and cannot do this; a number
    # can, so a number has to be checked.
    if (
        cfg.audio_ctx is not None
        and cfg.audio_ctx > 0
        and cfg.max_seconds > window_seconds(cfg.audio_ctx)
    ):
        raise RuntimeError(
            f"RUBAI_AUDIO_CTX={cfg.audio_ctx} hears only "
            f"{window_seconds(cfg.audio_ctx):.1f}s of audio, but "
            f"STT_MAX_SECONDS={cfg.max_seconds:g} accepts longer recordings. "
            "Lower STT_MAX_SECONDS to match, or raise the context."
        )

    if cfg.token is None and not cfg.allow_anonymous:
        # Refusing to start beats starting open. This service holds a model
        # anyone can spend CPU on, and it is reachable from wherever it is
        # deployed unless something says otherwise.
        raise RuntimeError(
            "STT_SERVICE_TOKEN is not set. Set it, or set "
            "RUBAI_ALLOW_ANONYMOUS=true for a private network deployment."
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if whisper is None:
            engine.spawn()
            await engine.wait_until_ready(cfg.startup_timeout)
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(title="Rubai STT", version="1.0.0", lifespan=lifespan)

    def authorised(header: str | None) -> bool:
        if cfg.token is None:
            return cfg.allow_anonymous
        if not header or not header.lower().startswith("bearer "):
            return False
        import hmac

        return hmac.compare_digest(header[7:].strip(), cfg.token)

    @app.get("/health")
    async def health() -> JSONResponse:
        """Open by design, and says nothing a caller could use.

        503 until the model answers, not merely until the port is open: the
        platform's health check is the only thing standing between a half-
        started container and live traffic, and a 200 that means "listening"
        makes it useless.
        """
        body = {
            "status": "ok" if engine.ready else "starting",
            "model_loaded": engine.ready,
            "model": Path(cfg.model_path).name,
            "language": cfg.language,
            "vad": cfg.vad,
        }
        return JSONResponse(status_code=200 if engine.ready else 503, content=body)

    @app.post("/inference")
    async def inference(
        request: Request,
        file: UploadFile = File(...),
        language: str | None = Form(default=None),
        response_format: str | None = Form(default=None),
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        if not authorised(authorization):
            return JSONResponse(
                status_code=401,
                content={"error": "This service requires a bearer token."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not engine.ready:
            return JSONResponse(
                status_code=503,
                content={"error": "The model is still loading."},
            )

        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cfg.max_bytes:
            return JSONResponse(
                status_code=413, content={"error": "The recording is too large."}
            )

        # The uploaded filename is read nowhere: names come from this process.
        workspace = Path(tempfile.mkdtemp(prefix="rubai-"))
        started = time.monotonic()
        try:
            source = workspace / "upload"
            size = 0
            with source.open("wb") as handle:
                while chunk := await file.read(1 << 20):
                    size += len(chunk)
                    if size > cfg.max_bytes:
                        return JSONResponse(
                            status_code=413,
                            content={"error": "The recording is too large."},
                        )
                    handle.write(chunk)
            if size == 0:
                return JSONResponse(
                    status_code=422, content={"error": "No audio was sent."}
                )

            wav = workspace / "audio.wav"
            try:
                convert_to_wav(source, wav)
            except AudioError as exc:
                return JSONResponse(
                    status_code=exc.status, content={"error": exc.message}
                )

            duration = wav_duration_seconds(wav)
            if duration > cfg.max_seconds:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": (
                            f"The recording is {duration:.0f}s, longer than the "
                            f"{cfg.max_seconds:.0f}s limit."
                        )
                    },
                )

            # One deadline for the whole request. Waiting for a free slot and
            # running the inference spend the same budget, because a queue wait
            # that buys a fresh full budget afterwards means this service can
            # answer in twice what it promised -- and the API in front has long
            # since given up.
            deadline = started + cfg.request_timeout
            try:
                await asyncio.wait_for(
                    gate.acquire(),
                    timeout=max(0.0, deadline - time.monotonic()),
                )
            except asyncio.TimeoutError:
                return JSONResponse(
                    status_code=503,
                    content={"error": "The service is busy. Try again shortly."},
                )
            window = (
                encoder_context_for(
                    duration,
                    headroom=cfg.audio_ctx_headroom,
                    floor=cfg.audio_ctx_floor,
                )
                if cfg.audio_ctx is None
                else cfg.audio_ctx
            )
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # The slot arrived after the budget was gone. Queueing is
                    # what ran out, so this is "busy", not "the model is slow".
                    return JSONResponse(
                        status_code=503,
                        content={"error": "The service is busy. Try again shortly."},
                    )
                text = await engine.transcribe(
                    wav, audio_ctx=window, timeout=remaining
                )
            except httpx.TimeoutException:
                # Distinct from a broken model on purpose: the API reads this
                # status to decide whether to tell the operator to try again.
                logger.warning(
                    "Transcription exceeded the %.0fs budget", cfg.request_timeout
                )
                return JSONResponse(
                    status_code=504,
                    content={"error": "The model took too long to transcribe this."},
                )
            except Exception:
                # The type, never the detail: an HTTP client puts URLs and
                # headers into both its message and its traceback.
                logger.exception("Transcription failed")
                return JSONResponse(
                    status_code=502,
                    content={"error": "The model failed to transcribe the audio."},
                )
            finally:
                # Always, including both returns above: a slot held by a request
                # that is already over wedges every request behind it.
                gate.release()

            return JSONResponse(
                content={
                    "text": text,
                    "language": language or cfg.language,
                    "duration_seconds": round(duration, 2),
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    # Which window this recording got. Not a secret, and the
                    # first thing to look at when a transcript loses its end.
                    "audio_ctx": window,
                }
            )
        finally:
            # Every byte this request wrote, gone before it answers — including
            # on the error paths above, which is why this is a finally and not
            # a line at the end of the happy path.
            shutil.rmtree(workspace, ignore_errors=True)

    return app


app_factory = create_app

if __name__ == "__main__":  # pragma: no cover - the container entrypoint
    import uvicorn

    configuration = Config.from_env()
    uvicorn.run(
        create_app(configuration),
        host="0.0.0.0",  # noqa: S104 - a container port is meant to be reachable
        port=configuration.port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )
