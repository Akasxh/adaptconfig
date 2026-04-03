"""
AuditLog ORM model for the finspark package.

Immutable audit trail — append-only, never updated or soft-deleted.
Captures every state-changing operation on any resource.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from finspark.models.base import Base, JSONBType, TimestampMixin, uuid_pk


class AuditLog(TimestampMixin, Base):
    """
    Append-only audit log.  No updates, no deletes.

    `resource_type` is the entity kind (e.g. 'Configuration', 'Simulation').
    `action` is the operation (e.g. 'created', 'deployed', 'deleted').
    """

    __tablename__ = "finspark_audit_logs"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
        comment="Nullable for system-level events",
    )
    # Who performed the action
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="user",
        comment="user | service | system | ai_agent",
    )
    # What happened
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="e.g. configuration.created, simulation.started, adapter.deployed",
    )
    # What resource was affected
    resource_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
        comment="ORM model name: Configuration, Simulation, AdapterVersion, etc.",
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        comment="PK of the affected resource row",
    )
    # Full JSONB snapshots; null for create (no before) / delete (no after)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(
        JSONBType(), nullable=True
    )
    after_state: Mapped[dict[str, Any] | None] = mapped_column(
        JSONBType(), nullable=True
    )
    # Request context
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    request_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="Correlation ID from the HTTP request",
    )
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONBType(),
        nullable=False,
        default=dict,
        server_default="{}",
    )
    outcome: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="success",
        comment="success | failure | partial",
    )
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_finspark_audit_logs_tenant_id", "tenant_id"),
        Index("ix_finspark_audit_logs_action", "action"),
        Index("ix_finspark_audit_logs_resource_type", "resource_type"),
        Index(
            "ix_finspark_audit_logs_resource",
            "resource_type",
            "resource_id",
        ),
        Index("ix_finspark_audit_logs_actor_id", "actor_id"),
        Index("ix_finspark_audit_logs_created_at", "created_at"),
        Index(
            "ix_finspark_audit_logs_tenant_created",
            "tenant_id",
            "created_at",
        ),
        Index("ix_finspark_audit_logs_request_id", "request_id"),
        CheckConstraint(
            "actor_type IN ('user','service','system','ai_agent')",
            name="ck_finspark_audit_logs_actor_type",
        ),
        CheckConstraint(
            "outcome IN ('success','failure','partial')",
            name="ck_finspark_audit_logs_outcome",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} action={self.action} "
            f"resource={self.resource_type}:{self.resource_id}>"
        )
