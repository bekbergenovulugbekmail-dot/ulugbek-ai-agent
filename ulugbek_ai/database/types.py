"""Portable column types.

The production target is PostgreSQL, but the schema is written against portable
types so the same models run on SQLite for fast unit tests:

* ``JSONBType``  — ``JSONB`` on PostgreSQL, ``JSON`` elsewhere.
* ``UUIDType``   — SQLAlchemy's ``Uuid``: native ``uuid`` on PostgreSQL,
  ``CHAR(32)`` elsewhere.
* ``DateTimeTZ`` — timezone-aware ``TIMESTAMP``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, TypeVar

from sqlalchemy import DateTime, JSON, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

E = TypeVar("E", bound=Enum)

#: JSON payloads — indexed/queryable JSONB on PostgreSQL.
JSONBType = JSON().with_variant(JSONB(), "postgresql")

#: Primary/foreign keys.
UUIDType = Uuid(as_uuid=True)

#: Timestamps. ``timezone=True`` keeps offsets on PostgreSQL.
DateTimeTZ = DateTime(timezone=True)


class EnumString(TypeDecorator):
    """A ``VARCHAR`` column that round-trips a Python enum.

    Storing enums as plain strings keeps migrations simple (adding a member
    needs no ``ALTER TYPE``) but a bare ``String`` column hands the value back as
    ``str``, so ``row.status is Status.RUNNING`` is quietly always ``False``.
    This decorator converts on the way in and on the way out, so model
    attributes are always real enum members.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type[E], length: int = 32) -> None:
        super().__init__(length=length)
        self.enum_class = enum_class

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if isinstance(value, Enum):
            return str(value.value)
        # Accept the raw value too, so a plain string still round-trips.
        return str(self.enum_class(value).value)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return self.enum_class(value)

    def copy(self, **kwargs: Any) -> "EnumString":
        return EnumString(self.enum_class, length=self.impl.length)
