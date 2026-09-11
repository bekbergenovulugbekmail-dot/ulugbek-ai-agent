"""Secret redaction.

Everything that leaves the process — logs, audit trace rows, API responses —
passes through here first. Redaction happens on two axes:

1. **Key based** — a mapping key that looks like a credential is masked whatever
   its value (``api_key``, ``password``, ``token``, ...).
2. **Value based** — known credential shapes are masked wherever they appear in
   free text (``sk-ant-...``, bearer tokens, postgres URLs with a password, ...).
"""

from __future__ import annotations

import re
from typing import Any, Final

REDACTED: Final[str] = "***REDACTED***"

#: Substrings that mark a mapping key as sensitive (case-insensitive).
SENSITIVE_KEY_PARTS: Final[tuple[str, ...]] = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "credential",
    "authorization",
    "auth",
    "session_key",
    "cookie",
)

#: ``(pattern, replacement)`` pairs applied to every string that is logged.
_VALUE_PATTERNS: Final[tuple[tuple[re.Pattern[str], str], ...]] = (
    # key=value / key: value pairs inside free text. An auth scheme keyword is
    # swallowed together with the token so "Authorization: Bearer x" masks once.
    (
        re.compile(
            r"(?i)\b(" + "|".join(SENSITIVE_KEY_PARTS) + r")\b(\s*[:=]\s*)"
            r"(?:bearer\s+|basic\s+|token\s+)?[\"']?[^\s\"',;}]{4,}"
        ),
        r"\1\2" + REDACTED,
    ),
    # Anthropic / OpenAI style keys
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), REDACTED),
    # GitHub tokens
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"), REDACTED),
    # Slack / bot tokens
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"), REDACTED),
    # JWTs
    (
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b"),
        REDACTED,
    ),
    # Authorization headers: "Bearer <token>"
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._\-=]{8,}"), r"\1 " + REDACTED),
    # Credentials embedded in a URL: scheme://user:password@host
    (
        re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^\s:/@]+):[^\s/@]+@"),
        r"\1:" + REDACTED + "@",
    ),
)

_MAX_DEPTH: Final[int] = 12


def is_sensitive_key(key: str) -> bool:
    """True if a mapping key names a credential."""
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_text(text: str) -> str:
    """Mask known credential shapes inside free text."""
    if not text:
        return text
    result = text
    for pattern, replacement in _VALUE_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact secrets from an arbitrary JSON-ish structure.

    Containers are copied, never mutated in place, so callers can safely redact
    a payload they still intend to use.
    """
    if _depth > _MAX_DEPTH:
        return "<max-depth>"

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {
            key: REDACTED if isinstance(key, str) and is_sensitive_key(key)
            else redact(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item, _depth=_depth + 1) for item in value]
    return redact_text(str(value))


def truncate(text: str, limit: int, *, suffix: str = "... [truncated]") -> str:
    """Shorten *text* to *limit* characters, marking that it was cut."""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(limit - len(suffix), 0)] + suffix
