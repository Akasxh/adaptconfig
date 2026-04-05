"""FastAPI application entry point."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, Response

from finspark.api.routes import (
    adapters,
    analytics,
    audit,
    configurations,
    documents,
    health,
    search,
    simulations,
    webhooks,
)
from finspark.core.config import settings
from finspark.core.database import init_db
from finspark.core.logging_filter import PIIMaskingFilter
from finspark.core.middleware import (
    DeprecationHeaderMiddleware,
    RequestLoggingMiddleware,
    TenantMiddleware,
)
from finspark.core.rate_limiter import RateLimiterMiddleware, metrics
from finspark.seeds import seed_adapters

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

# Wire PII masking into all log output
logging.getLogger().addFilter(PIIMaskingFilter())


async def _run_migrations() -> None:
    """Run Alembic migrations, falling back to create_all for dev."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "alembic", "upgrade", "head",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode == 0:
            logger.info("Alembic migrations applied successfully")
            return
        logger.warning(
            "Alembic migration failed (rc=%d): %s — falling back to create_all",
            proc.returncode,
            stderr.decode().strip(),
        )
    except Exception as exc:
        logger.warning("Alembic unavailable (%s) — falling back to create_all", exc)
    await init_db()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan - initialize DB and seed data on startup."""
    if settings.debug:
        await init_db()
    else:
        await _run_migrations()
    await seed_adapters()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)

    from finspark.core import events
    from finspark.services.webhook_delivery import deliver_event

    for event_type in [
        events.CONFIG_CREATED,
        events.CONFIG_UPDATED,
        events.CONFIG_DEPLOYED,
        events.CONFIG_ROLLED_BACK,
        events.SIMULATION_COMPLETED,
        events.DOCUMENT_PARSED,
    ]:
        events.on(
            event_type,
            lambda data, et=event_type: asyncio.create_task(
                deliver_event(data.get("tenant_id", "default"), et, data)
            ),
        )

    yield

    # Shutdown: close the LLM client connection pool if it was created
    from finspark.services.llm.client import _shared_client  # noqa: PLC0415

    if _shared_client is not None:
        await _shared_client.close()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject standard security headers into every response."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'"
        )
        return response


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI-Powered Integration Configuration Platform",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Middleware (order matters - last added = first executed)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(DeprecationHeaderMiddleware)
app.add_middleware(RateLimiterMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Tenant-ID", "X-Tenant-Name", "X-Tenant-Role"],
)

# Trusted host validation — disabled for cloud deployments (Railway, Render)
# where the host header varies. CORS + auth provide sufficient protection.
# if not settings.debug:
#     app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

# Routes
app.include_router(health.router)
app.include_router(documents.router, prefix="/api/v1")
app.include_router(adapters.router, prefix="/api/v1")
app.include_router(configurations.router, prefix="/api/v1")
app.include_router(simulations.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(search.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")
app.include_router(analytics.router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch all unhandled exceptions — log full traceback server-side,
    return a generic 500 to avoid leaking stack traces to clients."""
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/metrics")
async def get_metrics() -> dict:
    """Return in-memory API metrics."""
    return await metrics.snapshot()
