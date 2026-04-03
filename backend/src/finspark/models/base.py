"""Declarative base and shared mixins for all finspark ORM models."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, mapped_column
from sqlalchemy.types import JSON, TypeDecorator


# ---------------------------------------------------------------------------
# Dialect-adaptive JSONB shim
# ---------------------------------------------------------------------------


class JSONBType(TypeDecorator[dict[str, Any]]):
    """Uses PostgreSQL JSONB in production, falls back to JSON (SQLite)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        return value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return value


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(AsyncAttrs, DeclarativeBase):
    """Project-wide declarative base with async attribute support."""

    type_annotation_map: dict[Any, Any] = {
        dict[str, Any]: JSONBType(),
    }


# ---------------------------------------------------------------------------
# Mixins
# ---------------------------------------------------------------------------


class TimestampMixin:
    """Adds created_at / updated_at with server-side defaults."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )


class SoftDeleteMixin:
    """Soft-delete pattern: deleted_at + is_deleted flag."""

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        index=True,
    )

    def soft_delete(self) -> None:
        self.deleted_at = datetime.now(UTC)
        self.is_deleted = True

    def restore(self) -> None:
        self.deleted_at = None
        self.is_deleted = False


# ---------------------------------------------------------------------------
# Primary key helper
# ---------------------------------------------------------------------------


def uuid_pk() -> MappedColumn[str]:
    """UUID primary key stored as 36-char string (SQLite-compatible)."""
    return mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        nullable=False,
    )
