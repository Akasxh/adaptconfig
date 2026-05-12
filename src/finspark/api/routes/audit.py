"""Audit log routes."""

import json
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.dependencies import get_tenant_context
from finspark.core.database import get_db
from finspark.models.audit import AuditLog
from finspark.schemas.audit import AuditLogResponse
from finspark.schemas.common import APIResponse, PaginatedResponse, TenantContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("/", response_model=APIResponse[PaginatedResponse[AuditLogResponse]])
async def query_audit_logs(
    resource_type: str | None = None,
    resource_id: str | None = None,
    action: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> APIResponse[PaginatedResponse[AuditLogResponse]]:
    """Query audit logs for the current tenant."""
    filters = [AuditLog.tenant_id == tenant.tenant_id]

    if resource_type:
        filters.append(AuditLog.resource_type == resource_type)
    if resource_id:
        filters.append(AuditLog.resource_id == resource_id)
    if action:
        filters.append(AuditLog.action == action)

    # Count applies all active filters so pagination metadata is accurate
    count_stmt = select(func.count()).select_from(AuditLog).where(*filters)
    total = (await db.execute(count_stmt)).scalar() or 0

    # Paginated data uses the same filters
    stmt = (
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    items = []
    for log in logs:
        if log.details:
            try:
                details = json.loads(log.details)
            except (json.JSONDecodeError, ValueError):
                logger.warning("Malformed JSON in audit log %s details; returning None", log.id)
                details = None
        else:
            details = None
        items.append(
            AuditLogResponse(
                id=log.id,
                tenant_id=log.tenant_id,
                actor=log.actor,
                action=log.action,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                details=details,
                created_at=log.created_at,
            )
        )

    return APIResponse(
        data=PaginatedResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            has_next=(page * page_size) < total,
        ),
    )
