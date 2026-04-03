"""
Adapter and AdapterVersion ORM models for the finspark package.

Adapter       — catalogue entry for an integration type.
AdapterVersion — immutable versioned snapshot of an adapter's interface contract.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finspark.models.base import Base, JSONBType, SoftDeleteMixin, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from finspark.models.configuration import Configuration


class Adapter(TimestampMixin, SoftDeleteMixin, Base):
    """Registry entry for an external service adapter (CIBIL, GSTN, Razorpay…)."""

    __tablename__ = "finspark_adapters"

    id: Mapped[str] = uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Machine-readable identifier: cibil, gstn, razorpay",
    )
    category: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="bureau | kyc | payment | fraud | open_banking | gst | other",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONBType(),
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Relationships
    versions: Mapped[list["AdapterVersion"]] = relationship(
        "AdapterVersion",
        back_populates="adapter",
        lazy="noload",
    )

    __table_args__ = (
        UniqueConstraint("slug", name="uq_finspark_adapters_slug"),
        Index("ix_finspark_adapters_slug", "slug"),
        Index("ix_finspark_adapters_category", "category"),
        CheckConstraint(
            "category IN ('bureau','kyc','payment','fraud','open_banking','gst','other')",
            name="ck_finspark_adapters_category",
        ),
    )

    def __repr__(self) -> str:
        return f"<Adapter id={self.id} slug={self.slug}>"


class AdapterVersion(TimestampMixin, Base):
    """
    Immutable versioned snapshot of an Adapter's interface contract.

    Status lifecycle: draft → published → deprecated → retired.
    Once published this row must not be mutated.
    """

    __tablename__ = "finspark_adapter_versions"

    id: Mapped[str] = uuid_pk()
    adapter_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("finspark_adapters.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    semver: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="SemVer string: 1.0.0, 2.3.1-beta",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        comment="draft | published | deprecated | retired",
    )
    input_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONBType(),
        nullable=False,
        default=dict,
        server_default="{}",
        comment="JSON Schema for inbound request fields",
    )
    output_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONBType(),
        nullable=False,
        default=dict,
        server_default="{}",
        comment="JSON Schema for outbound response fields",
    )
    default_config: Mapped[dict[str, Any]] = mapped_column(
        JSONBType(),
        nullable=False,
        default=dict,
        server_default="{}",
        comment="Baseline config cloned for new Configurations",
    )
    is_breaking: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        comment="If True, auto-migration is blocked",
    )
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    adapter: Mapped["Adapter"] = relationship(
        "Adapter",
        back_populates="versions",
        lazy="noload",
    )
    configurations: Mapped[list["Configuration"]] = relationship(
        "Configuration",
        back_populates="adapter_version",
        lazy="noload",
    )

    __table_args__ = (
        UniqueConstraint(
            "adapter_id",
            "semver",
            name="uq_finspark_adapter_versions_adapter_semver",
        ),
        Index("ix_finspark_adapter_versions_adapter_id", "adapter_id"),
        Index("ix_finspark_adapter_versions_status", "status"),
        Index(
            "ix_finspark_adapter_versions_adapter_status",
            "adapter_id",
            "status",
        ),
        CheckConstraint(
            "status IN ('draft','published','deprecated','retired')",
            name="ck_finspark_adapter_versions_status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<AdapterVersion id={self.id} adapter={self.adapter_id} "
            f"semver={self.semver} status={self.status}>"
        )
