"""Observability routes for API call logging."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.dependencies import get_tenant_context
from finspark.core.database import get_db
from finspark.schemas.common import APIResponse, TenantContext
from finspark.schemas.observability import (
    APICallLogDetailResponse,
    APICallLogListResponse,
    APICallLogResponse,
    VersionComparisonResponse,
)
from finspark.services.observability.call_logger import CallLogger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/observability", tags=["Observability"])


@router.get("/calls", response_model=APIResponse[APICallLogListResponse])
async def list_calls(
    adapter_name: str | None = None,
    adapter_version: str | None = None,
    endpoint_path: str | None = None,
    status_min: int | None = None,
    status_max: int | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[APICallLogListResponse]:
    """List API call logs with optional filters."""
    call_logger = CallLogger(db)
    calls = await call_logger.get_calls(
        tenant_id=tenant.tenant_id,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        endpoint_path=endpoint_path,
        status_min=status_min,
        status_max=status_max,
        from_date=from_date,
        to_date=to_date,
        limit=limit,
        offset=offset,
    )
    total = await call_logger.count_calls(
        tenant_id=tenant.tenant_id,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        endpoint_path=endpoint_path,
        status_min=status_min,
        status_max=status_max,
        from_date=from_date,
        to_date=to_date,
    )
    items = [
        APICallLogResponse(
            id=c.id,
            tenant_id=c.tenant_id,
            configuration_id=c.configuration_id,
            adapter_name=c.adapter_name,
            adapter_version=c.adapter_version,
            endpoint_path=c.endpoint_path,
            http_method=c.http_method,
            response_status=c.response_status,
            response_time_ms=c.response_time_ms,
            schema_match=c.schema_match,
            error_code=c.error_code,
            error_message=c.error_message,
            created_at=c.created_at,
        )
        for c in calls
    ]
    return APIResponse(
        data=APICallLogListResponse(items=items, total=total, limit=limit, offset=offset),
    )


@router.get("/calls/{call_id}", response_model=APIResponse[APICallLogDetailResponse])
async def get_call_detail(
    call_id: str,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[APICallLogDetailResponse]:
    """Get a single API call log with full request/response detail."""
    call_logger = CallLogger(db)
    call = await call_logger.get_call_by_id(tenant.tenant_id, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call log not found")

    detail = APICallLogDetailResponse(
        id=call.id,
        tenant_id=call.tenant_id,
        configuration_id=call.configuration_id,
        adapter_name=call.adapter_name,
        adapter_version=call.adapter_version,
        endpoint_path=call.endpoint_path,
        http_method=call.http_method,
        response_status=call.response_status,
        response_time_ms=call.response_time_ms,
        schema_match=call.schema_match,
        error_code=call.error_code,
        error_message=call.error_message,
        created_at=call.created_at,
        request_headers=_parse_json(call.request_headers),
        request_body=_parse_json(call.request_body),
        response_headers=_parse_json(call.response_headers),
        response_body=_parse_json(call.response_body),
        drift_fields=_parse_json(call.drift_fields),
    )
    return APIResponse(data=detail)


@router.get("/compare/{adapter_name}", response_model=APIResponse[VersionComparisonResponse])
async def compare_versions(
    adapter_name: str,
    version_a: str = Query(...),
    version_b: str = Query(...),
    endpoint_path: str | None = None,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[VersionComparisonResponse]:
    """Compare response patterns between two adapter versions."""
    call_logger = CallLogger(db)
    comparison = await call_logger.compare_versions(
        tenant_id=tenant.tenant_id,
        adapter_name=adapter_name,
        version_a=version_a,
        version_b=version_b,
        endpoint_path=endpoint_path,
    )
    return APIResponse(
        data=VersionComparisonResponse(
            adapter_name=comparison["adapter_name"],
            version_a=comparison["version_a"],
            version_b=comparison["version_b"],
            endpoint_path=endpoint_path,
        ),
    )


def _parse_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Malformed JSON in call log field; returning None")
        return None
