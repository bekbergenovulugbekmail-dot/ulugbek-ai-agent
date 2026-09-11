"""Translation of domain exceptions into HTTP responses.

Handlers are registered once on the app, so no route needs a try/except and no
error can leak a stack trace or a secret to the client.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ulugbek_ai.core.errors import UlugbekError
from ulugbek_ai.core.redaction import redact_text

logger = logging.getLogger(__name__)


async def ulugbek_error_handler(
    request: Request, exc: UlugbekError
) -> JSONResponse:
    """Known domain error: report its code, message and safe details."""
    if exc.http_status >= 500:
        logger.error("%s on %s: %s", exc.code, request.url.path, exc.message)
    else:
        logger.info("%s on %s: %s", exc.code, request.url.path, exc.message)
    return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Anything else: log it in full, tell the client nothing specific."""
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "An internal error occurred.",
                "details": {},
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(UlugbekError, ulugbek_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)


def safe_message(exc: Exception) -> str:
    """Redacted string form of an exception, for logs."""
    return redact_text(str(exc))
