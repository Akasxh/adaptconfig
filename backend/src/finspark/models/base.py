"""Declarative base and shared mixin for all ORM models."""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, Select, func, true
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, mapped_column

if TYPE_CHECKING:
    pass


class Base(DeclarativeBase):
    """Single declarative base for the entire application."""

    type_annotation_map: dict[type, Any] = {}


class TimestampMixin:
    """Adds created_at / updated_at to any model."""

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


class UUIDMixin:
    """Primary-key as UUID string (SQLite-compatible)."""

    id: Mapped[str] = mapped_column(
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )


class SoftDeleteMixin:
    """Adds is_deleted flag; use _soft_delete_filter() to exclude deleted rows."""

    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )


def _soft_delete_filter(stmt: "Select[Any]", model: type[SoftDeleteMixin]) -> "Select[Any]":
    """
    Append ``WHERE is_deleted = false`` to *stmt* for any model that uses
    :class:`SoftDeleteMixin`.

    Usage::

        from sqlalchemy import select
        from finspark.models.base import _soft_delete_filter
        from finspark.models.tenant import Tenant

        stmt = _soft_delete_filter(select(Tenant), Tenant)
        result = await session.execute(stmt)
    """
    return stmt.where(model.is_deleted.is_(False))
