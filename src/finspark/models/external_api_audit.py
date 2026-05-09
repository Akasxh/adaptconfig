"""External API audit trail model for compliance-grade tracking."""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from finspark.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin


class ExternalAPIAudit(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Immutable, append-only audit record for every external API interaction.

    Uses cryptographic hash chaining for tamper detection.
    """

    __tablename__ = "external_api_audits"

    # WHO
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    configuration_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # WHAT
    adapter_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    adapter_version: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    endpoint_path: Mapped[str] = mapped_column(String(500), nullable=False)
    http_method: Mapped[str] = mapped_column(String(10), nullable=False)

    # REQUEST (PII masked)
    request_body_masked: Mapped[str | None] = mapped_column(Text, nullable=True)

    # RESPONSE (PII masked)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body_masked: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    # RESULT
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # WHY (what triggered this call)
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    trigger_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # CHAIN (workflow context)
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    workflow_step_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # TAMPER DETECTION
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)
