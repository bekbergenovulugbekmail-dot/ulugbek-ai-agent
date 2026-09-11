"""User ORM model."""

from __future__ import annotations

from typing import Any

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from ulugbek_ai.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from ulugbek_ai.database.types import JSONBType


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An owner of projects, tasks and memories.

    Authentication is intentionally out of scope for this phase; the
    ``external_id`` column is the hook a future auth provider binds to.
    """

    __tablename__ = "users"

    external_id: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    extra: Mapped[dict[str, Any]] = mapped_column(
        JSONBType, default=dict, nullable=False
    )
