"""Runtime API proxy routes."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.api.dependencies import get_tenant_context
from finspark.core.database import get_db
from finspark.schemas.common import TenantContext
from finspark.services.proxy.router import ProxyRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/proxy", tags=["Proxy"])


@router.api_route(
    "/{config_id}/{endpoint_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
)
async def proxy_request(
    config_id: str,
    endpoint_path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    tenant: TenantContext = Depends(get_tenant_context),
) -> JSONResponse:
    body: dict | None = None
    if request.method not in ("GET", "DELETE"):
        try:
            body = await request.json()
        except Exception:
            body = None

    incoming_headers = dict(request.headers)
    incoming_headers.pop("host", None)
    incoming_headers.pop("content-length", None)

    proxy = ProxyRouter(db)
    result = await proxy.proxy_request(
        config_id=config_id,
        endpoint_path=endpoint_path,
        tenant_id=tenant.tenant_id,
        request_body=body,
        request_headers=incoming_headers,
        request_method=request.method,
    )

    return JSONResponse(
        status_code=result.status_code,
        content={
            "success": result.success,
            "data": result.response_body,
            "response_time_ms": result.response_time_ms,
            "retries_attempted": result.retries_attempted,
            "error": result.error,
            "circuit_open": result.circuit_open,
        },
    )
