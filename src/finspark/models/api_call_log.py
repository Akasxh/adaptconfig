"""API call log model for observability tracking."""

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from finspark.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin


class APICallLog(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Immutable log entry for 3rd-party API calls."""

    __tablename__ = "api_call_logs"

    configuration_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    adapter_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    adapter_version: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    endpoint_path: Mapped[str] = mapped_column(String(500), nullable=False)
    http_method: Mapped[str] = mapped_column(String(10), nullable=False)

    request_headers: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_headers: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_time_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    schema_match: Mapped[bool] = mapped_column(Boolean, default=True)
    drift_fields: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
