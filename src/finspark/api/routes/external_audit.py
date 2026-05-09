"""External API audit trail routes."""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.dependencies import get_tenant_context
from finspark.core.database import get_db
from finspark.schemas.common import APIResponse, TenantContext
from finspark.schemas.external_audit import (
    AuditListResponse,
    ChainVerificationResponse,
    ExternalAPIAuditResponse,
)
from finspark.services.audit.external_audit import ExternalAuditService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/external-audit", tags=["External Audit"])


@router.get("/verify-chain", response_model=APIResponse[ChainVerificationResponse])
async def verify_chain(
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[ChainVerificationResponse]:
    """Verify the hash chain integrity for recent audit records."""
    service = ExternalAuditService(db)
    result = await service.verify_chain(tenant.tenant_id, limit=limit)
    return APIResponse(data=ChainVerificationResponse(**result))


@router.get("/export")
async def export_records(
    format: str = Query("json", pattern="^(json|csv)$"),
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> PlainTextResponse:
    """Export audit records for regulatory submission."""
    service = ExternalAuditService(db)
    content = await service.export_records(
        tenant_id=tenant.tenant_id,
        from_date=from_date,
        to_date=to_date,
        format=format,
    )
    media_type = "text/csv" if format == "csv" else "application/json"
    return PlainTextResponse(content=content, media_type=media_type)


@router.get("/", response_model=APIResponse[AuditListResponse])
async def list_records(
    adapter_name: str | None = None,
    adapter_version: str | None = None,
    success: bool | None = None,
    trigger_type: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[AuditListResponse]:
    """List external API audit records with filters."""
    service = ExternalAuditService(db)
    records = await service.get_records(
        tenant_id=tenant.tenant_id,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        success=success,
        trigger_type=trigger_type,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    total = await service.count_records(
        tenant_id=tenant.tenant_id,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        success=success,
        trigger_type=trigger_type,
        from_date=from_date,
        to_date=to_date,
    )
    items = [ExternalAPIAuditResponse.model_validate(r) for r in records]
    return APIResponse(data=AuditListResponse(items=items, total=total))


@router.get("/{record_id}", response_model=APIResponse[ExternalAPIAuditResponse])
async def get_record(
    record_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[ExternalAPIAuditResponse]:
    """Get a single external API audit record by ID."""
    service = ExternalAuditService(db)
    record = await service.get_record_by_id(tenant.tenant_id, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Audit record not found")
    return APIResponse(data=ExternalAPIAuditResponse.model_validate(record))
