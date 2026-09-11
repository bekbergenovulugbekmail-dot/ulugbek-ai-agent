"""Declarative base and reusable mixins."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ulugbek_ai.core.utils import new_id, utcnow
from ulugbek_ai.database.types import DateTimeTZ, UUIDType

#: Explicit naming convention so Alembic emits stable, nameable constraints.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Root of the ORM hierarchy."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def to_dict(self) -> dict[str, Any]:
        """Shallow column -> value mapping (no relationship traversal)."""
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


class UUIDPrimaryKeyMixin:
    """Application-generated UUID primary key."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=new_id
    )


class TimestampMixin:
    """``created_at`` / ``updated_at`` maintained by the ORM."""

    created_at: Mapped[datetime] = mapped_column(
        DateTimeTZ, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTimeTZ, default=utcnow, onupdate=utcnow, nullable=False
    )
