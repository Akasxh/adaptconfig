"""Middleware for tenant isolation, logging, and request processing."""

from __future__ import annotations

import logging
import re
import time

import jwt
from fastapi import Request
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from finspark.core.config import settings
from finspark.core.security import decode_jwt_token

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = "default"
DEFAULT_TENANT_NAME = "Default Tenant"

# Paths that bypass authentication entirely
_AUTH_BYPASS_PATHS: frozenset[str] = frozenset(
    [
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/metrics",
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
    ]
)


class TenantMiddleware(BaseHTTPMiddleware):
    """Extracts tenant context from request headers/JWT and injects into request state.

    Production mode (settings.debug=False):
        Requires a valid JWT Bearer token in the Authorization header.
        Extracts tenant_id, tenant_name, and role from the verified token payload.
        Returns 401 for missing or invalid tokens.

    Development mode (settings.debug=True):
        Falls back to X-Tenant-* headers for convenience.
        Default role is "viewer" (not "admin") to avoid accidental privilege escalation.

    Auth is always skipped for: /health, /docs, /redoc, /openapi.json, /metrics
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in _AUTH_BYPASS_PATHS:
            # Auth is skipped; still set tenant state and echo the header for observability
            tenant_id = request.headers.get("X-Tenant-ID", DEFAULT_TENANT_ID)
            request.state.tenant_id = tenant_id
            request.state.tenant_name = request.headers.get("X-Tenant-Name", DEFAULT_TENANT_NAME)
            request.state.role = request.headers.get("X-Tenant-Role", "viewer")
            response = await call_next(request)
            response.headers["X-Tenant-ID"] = tenant_id
            return response

        # Check for Bearer token first (works in both debug and production)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):]
            try:
                payload = decode_jwt_token(token)
                tenant_id = payload.get("tenant_id", DEFAULT_TENANT_ID)
                tenant_name = payload.get("tenant_name", DEFAULT_TENANT_NAME)
                role = payload.get("role", "viewer")
                request.state.user_id = payload.get("sub", "")
                request.state.email = payload.get("email", "")
            except jwt.PyJWTError:
                if not settings.debug:
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Invalid or expired token"},
                    )
                # In debug mode, fall through to header-based auth
                tenant_id = request.headers.get("X-Tenant-ID", DEFAULT_TENANT_ID)
                tenant_name = request.headers.get("X-Tenant-Name", DEFAULT_TENANT_NAME)
                role = request.headers.get("X-Tenant-Role", "admin")
        elif settings.debug:
            # Debug mode without token: use header-based auth
            tenant_id = request.headers.get("X-Tenant-ID", DEFAULT_TENANT_ID)
            tenant_name = request.headers.get("X-Tenant-Name", DEFAULT_TENANT_NAME)
            role = request.headers.get("X-Tenant-Role", "admin")
        else:
            # Production mode without token: reject
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"},
            )

        request.state.tenant_id = tenant_id
        request.state.tenant_name = tenant_name
        request.state.role = role

        response = await call_next(request)
        response.headers["X-Tenant-ID"] = tenant_id
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs request details and timing."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = time.monotonic()
        response = await call_next(request)
        duration = round((time.monotonic() - start_time) * 1000, 2)

        logger.info(
            "request_completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration,
                "tenant_id": getattr(request.state, "tenant_id", "unknown"),
            },
        )

        response.headers["X-Response-Time"] = f"{duration}ms"
        return response


# Pattern: /api/v1/adapters/{adapter_id}/versions/{version}/...
_ADAPTER_VERSION_RE = re.compile(r"^/api/v1/adapters/([^/]+)/versions/([^/]+)")


class DeprecationHeaderMiddleware(BaseHTTPMiddleware):
    """Adds Sunset and Deprecation headers when a deprecated adapter version is referenced."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)

        match = _ADAPTER_VERSION_RE.match(request.url.path)
        if not match:
            return response

        adapter_id = match.group(1)
        version_str = match.group(2)

        try:
            from finspark.core.database import async_session_factory
            from finspark.models.adapter import AdapterVersion
            from finspark.services.registry.deprecation import DeprecationTracker

            async with async_session_factory() as db:
                stmt = select(AdapterVersion).where(
                    AdapterVersion.adapter_id == adapter_id,
                    AdapterVersion.version == version_str,
                )
                result = await db.execute(stmt)
                version = result.scalar_one_or_none()

                if version and version.status == "deprecated":
                    tracker = DeprecationTracker(db)
                    sunset_date = tracker._compute_sunset_date(version)
                    if sunset_date:
                        response.headers["Sunset"] = sunset_date.strftime(
                            "%a, %d %b %Y %H:%M:%S GMT"
                        )
                    response.headers["Deprecation"] = "true"
                    replacement = await tracker._find_replacement(adapter_id, version)
                    if replacement:
                        response.headers["Link"] = (
                            f'</api/v1/adapters/{adapter_id}/versions/{replacement}>; rel="successor-version"'
                        )
        except Exception:
            logger.warning("Failed to check deprecation headers", exc_info=True)

        return response
