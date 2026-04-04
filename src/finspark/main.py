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
    await _seed_adapters()
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
    description="AI-Assisted Integration Configuration & Orchestration Engine for Enterprise Lending Platforms",
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

# Trusted host validation (production only)
if not settings.debug:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

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


async def _seed_adapters() -> None:
    """Seed the database with pre-built adapters for demo."""
    from sqlalchemy import select

    from finspark.core.database import async_session_factory
    from finspark.models.adapter import Adapter
    from finspark.services.registry.adapter_registry import AdapterRegistry

    async with async_session_factory() as db:
        # Check if already seeded
        result = await db.execute(select(Adapter).limit(1))
        if result.scalar_one_or_none():
            return

        registry = AdapterRegistry(db)

        # 1. Credit Bureau - CIBIL
        cibil = await registry.create_adapter(
            name="CIBIL Credit Bureau",
            category="bureau",
            description="TransUnion CIBIL credit score and report integration",
            icon="credit-card",
        )
        await registry.add_version(
            adapter_id=cibil.id,
            version="v1",
            base_url="https://api.cibil.com/v1",
            auth_type="api_key_certificate",
            endpoints=[
                {"path": "/credit-score", "method": "POST", "description": "Fetch credit score"},
                {
                    "path": "/credit-report",
                    "method": "POST",
                    "description": "Fetch detailed credit report",
                },
                {"path": "/bulk-inquiry", "method": "POST", "description": "Bulk credit inquiry"},
            ],
            request_schema={
                "type": "object",
                "required": ["pan_number", "full_name", "date_of_birth"],
                "properties": {
                    "pan_number": {"type": "string", "description": "PAN card number"},
                    "full_name": {"type": "string", "description": "Full name of applicant"},
                    "date_of_birth": {
                        "type": "string",
                        "format": "date",
                        "description": "Date of birth",
                    },
                    "mobile_number": {"type": "string", "description": "Mobile number"},
                    "email_address": {"type": "string", "description": "Email address"},
                    "address": {"type": "string", "description": "Residential address"},
                    "loan_type": {"type": "string", "description": "Type of loan"},
                    "loan_amount": {"type": "number", "description": "Requested loan amount"},
                },
            },
            response_schema={
                "type": "object",
                "properties": {
                    "credit_score": {"type": "integer"},
                    "score_range": {"type": "string"},
                    "enquiry_id": {"type": "string"},
                    "report_id": {"type": "string"},
                    "active_accounts": {"type": "integer"},
                    "overdue_accounts": {"type": "integer"},
                },
            },
        )
        await registry.add_version(
            adapter_id=cibil.id,
            version="v2",
            base_url="https://api.cibil.com/v2",
            auth_type="oauth2",
            endpoints=[
                {"path": "/scores", "method": "POST", "description": "Fetch credit score (v2)"},
                {"path": "/reports", "method": "POST", "description": "Fetch credit report (v2)"},
                {
                    "path": "/batch/inquiries",
                    "method": "POST",
                    "description": "Batch credit inquiry",
                },
                {"path": "/consent/verify", "method": "POST", "description": "Verify consent"},
            ],
            request_schema={
                "type": "object",
                "required": ["pan_number", "applicant_name", "dob", "consent_id"],
                "properties": {
                    "pan_number": {"type": "string"},
                    "applicant_name": {"type": "string"},
                    "dob": {"type": "string", "format": "date"},
                    "phone": {"type": "string"},
                    "email": {"type": "string"},
                    "residential_address": {"type": "string"},
                    "product_type": {"type": "string"},
                    "requested_amount": {"type": "number"},
                    "consent_id": {"type": "string"},
                },
            },
            changelog="Added consent verification, batch inquiries, OAuth2 auth",
        )

        # 2. KYC Provider - eKYC
        ekyc = await registry.create_adapter(
            name="Aadhaar eKYC Provider",
            category="kyc",
            description="Aadhaar-based electronic KYC verification",
            icon="shield-check",
        )
        await registry.add_version(
            adapter_id=ekyc.id,
            version="v1",
            base_url="https://api.ekyc-provider.com/v1",
            auth_type="api_key",
            endpoints=[
                {
                    "path": "/verify/aadhaar",
                    "method": "POST",
                    "description": "Verify Aadhaar number",
                },
                {"path": "/verify/pan", "method": "POST", "description": "Verify PAN card"},
                {
                    "path": "/digilocker/fetch",
                    "method": "POST",
                    "description": "Fetch DigiLocker documents",
                },
            ],
            request_schema={
                "type": "object",
                "required": ["aadhaar_number", "customer_name"],
                "properties": {
                    "aadhaar_number": {"type": "string"},
                    "customer_name": {"type": "string"},
                    "pan_number": {"type": "string"},
                    "date_of_birth": {"type": "string", "format": "date"},
                    "mobile_number": {"type": "string"},
                    "consent": {"type": "boolean"},
                },
            },
        )

        # 3. GST Verification
        gst = await registry.create_adapter(
            name="GST Verification Service",
            category="gst",
            description="GSTN verification and return filing status",
            icon="building",
        )
        await registry.add_version(
            adapter_id=gst.id,
            version="v1",
            base_url="https://api.gst-verify.com/v1",
            auth_type="api_key",
            endpoints=[
                {"path": "/verify/gstin", "method": "POST", "description": "Verify GSTIN"},
                {
                    "path": "/returns/status",
                    "method": "GET",
                    "description": "Check return filing status",
                },
                {"path": "/profile", "method": "GET", "description": "Get taxpayer profile"},
            ],
            request_schema={
                "type": "object",
                "required": ["gstin"],
                "properties": {
                    "gstin": {"type": "string", "description": "GST Identification Number"},
                    "financial_year": {"type": "string"},
                    "return_type": {"type": "string"},
                },
            },
        )

        # 4. Payment Gateway - Razorpay-like
        payment = await registry.create_adapter(
            name="Payment Gateway",
            category="payment",
            description="Payment processing and disbursement",
            icon="wallet",
        )
        await registry.add_version(
            adapter_id=payment.id,
            version="v1",
            base_url="https://api.payment-gateway.com/v1",
            auth_type="api_key",
            endpoints=[
                {"path": "/payments/create", "method": "POST", "description": "Create payment"},
                {"path": "/payments/{id}", "method": "GET", "description": "Get payment status"},
                {
                    "path": "/transfers/create",
                    "method": "POST",
                    "description": "Create bank transfer",
                },
                {"path": "/refunds/create", "method": "POST", "description": "Create refund"},
            ],
            request_schema={
                "type": "object",
                "required": ["amount", "account_number", "ifsc_code"],
                "properties": {
                    "amount": {"type": "number"},
                    "account_number": {"type": "string"},
                    "ifsc_code": {"type": "string"},
                    "beneficiary_name": {"type": "string"},
                    "reference_id": {"type": "string"},
                    "payment_mode": {"type": "string", "enum": ["NEFT", "IMPS", "RTGS", "UPI"]},
                    "vpa": {"type": "string"},
                },
            },
        )

        # 5. Fraud Detection
        fraud = await registry.create_adapter(
            name="Fraud Detection Engine",
            category="fraud",
            description="Real-time fraud scoring and risk assessment",
            icon="alert-triangle",
        )
        await registry.add_version(
            adapter_id=fraud.id,
            version="v1",
            base_url="https://api.fraud-detect.com/v1",
            auth_type="api_key",
            endpoints=[
                {"path": "/score", "method": "POST", "description": "Get fraud risk score"},
                {
                    "path": "/verify/device",
                    "method": "POST",
                    "description": "Device fingerprint check",
                },
                {"path": "/verify/velocity", "method": "POST", "description": "Velocity check"},
            ],
            request_schema={
                "type": "object",
                "required": ["customer_id", "transaction_amount"],
                "properties": {
                    "customer_id": {"type": "string"},
                    "transaction_amount": {"type": "number"},
                    "device_id": {"type": "string"},
                    "ip_address": {"type": "string"},
                    "mobile_number": {"type": "string"},
                    "email_address": {"type": "string"},
                },
            },
        )

        # 6. SMS Notification
        sms = await registry.create_adapter(
            name="SMS Gateway",
            category="notification",
            description="SMS notification delivery service",
            icon="message-square",
        )
        await registry.add_version(
            adapter_id=sms.id,
            version="v1",
            base_url="https://api.sms-gateway.com/v1",
            auth_type="api_key",
            endpoints=[
                {"path": "/send", "method": "POST", "description": "Send SMS"},
                {"path": "/status/{id}", "method": "GET", "description": "Check delivery status"},
                {"path": "/templates", "method": "GET", "description": "List SMS templates"},
            ],
            request_schema={
                "type": "object",
                "required": ["mobile_number", "message"],
                "properties": {
                    "mobile_number": {"type": "string"},
                    "message": {"type": "string"},
                    "template_id": {"type": "string"},
                    "sender_id": {"type": "string"},
                },
            },
        )

        # 7. Account Aggregator (AA Framework)
        aa = await registry.create_adapter(
            name="Account Aggregator (AA Framework)",
            category="open_banking",
            description="RBI-regulated Account Aggregator for consented financial data sharing",
            icon="link",
        )
        await registry.add_version(
            adapter_id=aa.id,
            version="v1",
            base_url="https://api.account-aggregator.com/v1",
            auth_type="mutual_tls",
            endpoints=[
                {
                    "path": "/consent/create",
                    "method": "POST",
                    "description": "Create consent request",
                },
                {
                    "path": "/consent/{id}/status",
                    "method": "GET",
                    "description": "Check consent status",
                },
                {
                    "path": "/fi/fetch",
                    "method": "POST",
                    "description": "Fetch financial information",
                },
                {
                    "path": "/fi/{session_id}",
                    "method": "GET",
                    "description": "Get FI data",
                },
            ],
            request_schema={
                "type": "object",
                "required": ["customer_vua", "fi_types", "consent_duration"],
                "properties": {
                    "customer_vua": {
                        "type": "string",
                        "description": "Customer's Virtual User Address (e.g., user@aa-provider)",
                    },
                    "fi_types": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "DEPOSIT",
                                "MUTUAL_FUNDS",
                                "INSURANCE",
                                "TERM_DEPOSIT",
                                "RECURRING_DEPOSIT",
                                "SIP",
                                "GOVT_SECURITIES",
                                "EQUITIES",
                                "ETF",
                            ],
                        },
                        "description": "Financial information types to fetch",
                    },
                    "consent_duration": {
                        "type": "string",
                        "description": "ISO 8601 duration for consent validity (e.g., P1Y)",
                    },
                    "data_range": {
                        "type": "object",
                        "properties": {
                            "from": {
                                "type": "string",
                                "format": "date",
                                "description": "Start date for data fetch",
                            },
                            "to": {
                                "type": "string",
                                "format": "date",
                                "description": "End date for data fetch",
                            },
                        },
                        "description": "Date range for financial data",
                    },
                    "purpose": {
                        "type": "string",
                        "description": "Purpose code as per AA spec (e.g., 101 for wealth management)",
                    },
                    "fip_id": {
                        "type": "string",
                        "description": "Financial Information Provider ID",
                    },
                },
            },
            response_schema={
                "type": "object",
                "properties": {
                    "consent_handle": {"type": "string"},
                    "consent_status": {
                        "type": "string",
                        "enum": ["PENDING", "APPROVED", "REJECTED", "REVOKED", "EXPIRED"],
                    },
                    "session_id": {"type": "string"},
                    "fi_data": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "fip_id": {"type": "string"},
                                "fi_type": {"type": "string"},
                                "data": {"type": "object"},
                            },
                        },
                    },
                },
            },
        )

        # 8. Email Notification Gateway
        email = await registry.create_adapter(
            name="Email Notification Gateway",
            category="notification",
            description="Email notification delivery and template management service",
            icon="mail",
        )
        await registry.add_version(
            adapter_id=email.id,
            version="v1",
            base_url="https://api.email-gateway.com/v1",
            auth_type="api_key",
            endpoints=[
                {"path": "/send", "method": "POST", "description": "Send email notification"},
                {
                    "path": "/status/{id}",
                    "method": "GET",
                    "description": "Check email delivery status",
                },
                {"path": "/templates", "method": "GET", "description": "List email templates"},
            ],
            request_schema={
                "type": "object",
                "required": ["to", "subject", "body"],
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address"},
                    "subject": {"type": "string", "description": "Email subject line"},
                    "body": {"type": "string", "description": "Email body (HTML or plain text)"},
                    "template_id": {"type": "string", "description": "Email template identifier"},
                    "cc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "CC recipients",
                    },
                    "attachments": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Attachment URLs",
                    },
                },
            },
        )

        await db.commit()
        logging.getLogger(__name__).info("Seeded 8 adapters with versions")
