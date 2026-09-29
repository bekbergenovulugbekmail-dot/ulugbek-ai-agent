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


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


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
            request_timeout=_float("RUBAI_REQUEST_TIMEOUT_SECONDS", 120.0),
            startup_timeout=_float("RUBAI_STARTUP_TIMEOUT_SECONDS", 300.0),
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

    def __init__(self, config: Config) -> None:
        self._config = config
        self._process: subprocess.Popen[bytes] | None = None
        self._client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{config.whisper_port}",
            timeout=config.request_timeout,
        )
        self.ready = False

    def spawn(self) -> None:
        cfg = self._config
        if not Path(cfg.model_path).exists():
            raise RuntimeError(f"model file missing at {cfg.model_path}")
        logger.info("Starting whisper-server on 127.0.0.1:%d", cfg.whisper_port)
        self._process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [
                cfg.whisper_bin,
                "--model",
                cfg.model_path,
                "--host",
                "127.0.0.1",
                "--port",
                str(cfg.whisper_port),
                "--threads",
                str(cfg.threads),
                "--language",
                cfg.language,
                "--no-timestamps",
                # Whisper's auto-detect mistakes Uzbek for Arabic-script
                # languages often enough that the language is pinned, not
                # detected. The model is monolingual in practice anyway.
                "--print-progress",
                "false",
            ],
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

    async def transcribe(self, wav: Path) -> str:
        with wav.open("rb") as handle:
            response = await self._client.post(
                "/inference",
                files={"file": ("audio.wav", handle, "audio/wav")},
                data={
                    "temperature": "0.0",
                    "response_format": "json",
                    "language": self._config.language,
                },
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

            try:
                await asyncio.wait_for(gate.acquire(), timeout=cfg.request_timeout)
            except asyncio.TimeoutError:
                return JSONResponse(
                    status_code=503,
                    content={"error": "The service is busy. Try again shortly."},
                )
            try:
                text = await engine.transcribe(wav)
            except Exception:
                # The type, never the detail: an HTTP client puts URLs and
                # headers into both its message and its traceback.
                logger.exception("Transcription failed")
                return JSONResponse(
                    status_code=502,
                    content={"error": "The model failed to transcribe the audio."},
                )
            finally:
                gate.release()

            return JSONResponse(
                content={
                    "text": text,
                    "language": language or cfg.language,
                    "duration_seconds": round(duration, 2),
                    "latency_ms": int((time.monotonic() - started) * 1000),
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
