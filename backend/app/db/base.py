"""Declarative base and portable column types.

Production runs on PostgreSQL. The test suite runs on SQLite so that
``pytest`` needs no services (§40), so every column type used here must be
expressible on both. Postgres-specific types are declared with an explicit
SQLite variant rather than being avoided.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import JSON, DateTime, MetaData, String, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# JSONB on Postgres, JSON on SQLite.
JSONType = JSONB().with_variant(JSON(), "sqlite")

# Naming convention so Alembic can autogenerate stable constraint names.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator):
    """A timezone-aware datetime that stays aware on SQLite too.

    SQLite drops tzinfo on the way out. Without this, comparing a stored
    ``expires_at`` against ``datetime.now(timezone.utc)`` raises in tests but
    not in production — exactly the kind of drift the test suite exists to
    catch.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime passed to a UTCDateTime column")
        return value.astimezone(dt.UTC)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)


ID = String(40)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
