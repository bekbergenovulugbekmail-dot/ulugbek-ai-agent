"""Small helpers with no domain knowledge."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp. Never use ``datetime.utcnow()``."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime read back from the database.

    Postgres returns timezone-aware values for ``TIMESTAMPTZ``; SQLite and some
    drivers drop the offset. Comparing the two kinds raises ``TypeError``, so
    every stored timestamp is normalized here before it is used in a comparison.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def new_id() -> uuid.UUID:
    """Primary key factory — UUID4, generated application-side."""
    return uuid.uuid4()


def coerce_uuid(value: str | uuid.UUID) -> uuid.UUID:
    """Parse *value* into a UUID, raising ``ValueError`` when malformed."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))
