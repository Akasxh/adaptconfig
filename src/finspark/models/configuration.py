"""Integration configuration models."""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from finspark.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin


class Configuration(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Generated integration configuration for a tenant."""

    __tablename__ = "configurations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter_version_id: Mapped[str] = mapped_column(
        ForeignKey("adapter_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="draft"
    )  # draft, configured, validating, testing, active, deprecated, rollback
    version: Mapped[int] = mapped_column(Integer, default=1)
    field_mappings: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    transformation_rules: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    hooks: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    auth_config: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON (encrypted)
    full_config: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON - complete config
    last_transform_run: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON snapshot of most recent /transform call
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConfigurationHistory(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Version history for configuration changes."""

    __tablename__ = "configuration_history"

    configuration_id: Mapped[str] = mapped_column(
        ForeignKey("configurations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    change_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # created, updated, status_change
    previous_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    changed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
