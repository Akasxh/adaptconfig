"""
Configuration and ConfigurationHistory ORM models.

Configuration       — live config record for a tenant + adapter pairing.
ConfigurationHistory — append-only history table; every change to Configuration
                       creates a new history row.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finspark.models.base import Base, JSONBType, SoftDeleteMixin, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from finspark.models.adapter import AdapterVersion
    from finspark.models.document import Document
    from finspark.models.simulation import Simulation


class Configuration(TimestampMixin, SoftDeleteMixin, Base):
    """
    Live configuration for a tenant+adapter pairing.

    `payload` is the merged, resolved configuration blob sent to the adapter
    at runtime.  Every change creates a new ConfigurationHistory row.
    """

    __tablename__ = "finspark_configurations"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
        comment="Owning tenant",
    )
    adapter_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("finspark_adapter_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        comment="draft | validated | deployed | failed | archived",
        index=True,
    )
    environment: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="sandbox",
        comment="sandbox | staging | production",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONBType(),
        nullable=False,
        default=dict,
        server_default="{}",
        comment="Resolved configuration data",
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="Monotonically increasing per (tenant_id, adapter_version_id)",
    )
    source: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="manual",
        comment="manual | ai_generated | imported | rollback",
    )
    # Optional: document IDs that seeded this config (denormalized for audit)
    source_document_ids: Mapped[list] = mapped_column(
        JSONBType(),
        nullable=False,
        default=list,
        server_default="[]",
    )

    # Relationships
    adapter_version: Mapped["AdapterVersion"] = relationship(
        "AdapterVersion",
        back_populates="configurations",
        lazy="noload",
    )
    history: Mapped[list["ConfigurationHistory"]] = relationship(
        "ConfigurationHistory",
        back_populates="configuration",
        lazy="noload",
        order_by="ConfigurationHistory.version.desc()",
    )
    simulations: Mapped[list["Simulation"]] = relationship(
        "Simulation",
        back_populates="configuration",
        lazy="noload",
    )

    __table_args__ = (
        Index("ix_finspark_configurations_tenant_id", "tenant_id"),
        Index("ix_finspark_configurations_adapter_version_id", "adapter_version_id"),
        Index("ix_finspark_configurations_status", "status"),
        Index(
            "ix_finspark_configurations_tenant_status",
            "tenant_id",
            "status",
        ),
        CheckConstraint(
            "status IN ('draft','validated','deployed','failed','archived')",
            name="ck_finspark_configurations_status",
        ),
        CheckConstraint(
            "source IN ('manual','ai_generated','imported','rollback')",
            name="ck_finspark_configurations_source",
        ),
        CheckConstraint(
            "environment IN ('sandbox','staging','production')",
            name="ck_finspark_configurations_environment",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Configuration id={self.id} tenant={self.tenant_id} "
            f"status={self.status} v={self.version}>"
        )


class ConfigurationHistory(TimestampMixin, Base):
    """
    Immutable configuration history snapshot — append-only.

    Never UPDATE this table.  Every config change = new row.
    """

    __tablename__ = "finspark_configuration_history"

    id: Mapped[str] = uuid_pk()
    configuration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("finspark_configurations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Monotonically increasing per configuration_id",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONBType(),
        nullable=False,
        default=dict,
        comment="Full resolved config data at this point in time",
    )
    diff_patch: Mapped[dict[str, Any] | None] = mapped_column(
        JSONBType(),
        nullable=True,
        comment="JSON Patch (RFC 6902) from previous version; null for v1",
    )
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_source: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="manual",
        comment="manual | ai_generated | imported | rollback",
    )

    # Relationships
    configuration: Mapped["Configuration"] = relationship(
        "Configuration",
        back_populates="history",
        lazy="noload",
    )

    __table_args__ = (
        UniqueConstraint(
            "configuration_id",
            "version",
            name="uq_finspark_config_history_config_version",
        ),
        Index("ix_finspark_config_history_configuration_id", "configuration_id"),
        CheckConstraint(
            "change_source IN ('manual','ai_generated','imported','rollback')",
            name="ck_finspark_config_history_change_source",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ConfigurationHistory id={self.id} "
            f"config={self.configuration_id} v={self.version}>"
        )
