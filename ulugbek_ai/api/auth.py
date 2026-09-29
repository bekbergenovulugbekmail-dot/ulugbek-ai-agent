"""Operator authentication.

One deployment, one operator, one shared secret held in the environment. That
is the whole model, and it is chosen rather than defaulted to: this system has
a single user, creates everything under one identity, and has no sign-up,
password reset or session list to justify.

Three things follow from it, and each is a deliberate decision:

* **The secret is compared in constant time.** A byte-by-byte comparison leaks
  the token through timing, slowly but surely, to anyone willing to measure.
* **The secret is never the Anthropic key.** A credential that pays for model
  calls must not also open the front door; ``ANTHROPIC_API_KEY`` is rejected
  as a user token by name, because reaching for the key already in the
  environment is the obvious shortcut.
* **The stream gets its own token.** ``EventSource`` cannot send an
  ``Authorization`` header, so the live activity feed would either go
  unauthenticated or carry the operator's token in a URL — where it lands in
  browser history, proxy logs and every ``Referer``. Instead the operator
  exchanges their token for one that is signed, scoped to streaming, and
  expires in minutes.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Final

#: Short enough to type, long enough that guessing is hopeless. A token below
#: this is refused at startup rather than quietly protecting nothing.
MIN_TOKEN_LENGTH: Final[int] = 32

_STREAM_PURPOSE: Final[bytes] = b"stream"


@dataclass(slots=True, frozen=True)
class Principal:
    """Who is making the request."""

    subject: str = "operator"
    is_authenticated: bool = False


#: The one authenticated identity this deployment has.
OPERATOR: Final[Principal] = Principal(subject="operator", is_authenticated=True)


def configured_token_problem(token: str | None) -> str | None:
    """Why the configured operator token cannot be trusted, if it cannot.

    Returned as a sentence, never with any part of the value: this text reaches
    logs and API responses.
    """
    if token is None:
        return "it is not set"
    if len(token) < MIN_TOKEN_LENGTH:
        return (
            f"it is shorter than {MIN_TOKEN_LENGTH} characters, which is short "
            "enough to guess"
        )
    return None


def token_matches(presented: str, configured: str) -> bool:
    """Constant-time comparison of a presented token against the real one."""
    return hmac.compare_digest(presented.encode(), configured.encode())


def bearer_from_header(header: str | None) -> str | None:
    """The token out of an ``Authorization`` header, or ``None``."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


# ------------------------------------------------------------------ stream #
def _sign(secret: str, expires_at: int) -> str:
    message = b"%s:%d" % (_STREAM_PURPOSE, expires_at)
    return hmac.new(secret.encode(), message, sha256).hexdigest()


def issue_stream_token(
    secret: str, *, ttl_seconds: float, now: float | None = None
) -> str:
    """A token that only opens an event stream, and only for a few minutes.

    Signed with the operator's token rather than stored, so it survives a
    restart and needs no table: the signature is the record.
    """
    expires_at = int((time.time() if now is None else now) + ttl_seconds)
    return f"{expires_at}.{_sign(secret, expires_at)}"


def stream_token_problem(
    secret: str, token: str | None, *, now: float | None = None
) -> str | None:
    """Why *token* does not open a stream, or ``None`` if it does."""
    if not token:
        return "no stream token was given"

    expiry_text, _, signature = token.partition(".")
    if not signature:
        return "the stream token is malformed"
    try:
        expires_at = int(expiry_text)
    except ValueError:
        return "the stream token is malformed"

    # Signature first: a token whose signature is wrong must not be told
    # whether its expiry was plausible.
    if not hmac.compare_digest(signature, _sign(secret, expires_at)):
        return "the stream token is not valid"
    if expires_at <= (time.time() if now is None else now):
        return "the stream token has expired"
    return None
