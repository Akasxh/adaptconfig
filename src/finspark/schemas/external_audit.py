"""Schemas for external API audit trail."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ExternalAPIAuditResponse(BaseModel):
    """Response schema for a single external API audit record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    configuration_id: str
    adapter_name: str
    adapter_version: str
    endpoint_path: str
    http_method: str
    response_status: int
    response_time_ms: int
    success: bool
    trigger_type: str
    error_code: str | None = None
    created_at: datetime


class AuditListResponse(BaseModel):
    """Paginated list of audit records."""

    items: list[ExternalAPIAuditResponse]
    total: int


class ChainVerificationResponse(BaseModel):
    """Result of hash chain integrity verification."""

    valid: bool
    records_checked: int
    first_broken: str | None = None
