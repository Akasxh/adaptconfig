"""
Shared pytest fixtures for all test scopes.

Design decisions:
- Uses SQLite+aiosqlite by default so tests run without a Postgres instance.
  Set TEST_DATABASE_URL env var to point at Postgres for CI/integration runs.
- Each test gets an isolated transaction that rolls back at teardown — zero
  data leakage between tests without per-test table truncation.
- The FastAPI app's `get_db` dependency is overridden to use the test session
  so every route in integration tests sees the same rolled-back DB state.
- LLM and external-HTTP mocks are opt-in fixtures, not module-level patches.
"""

from __future__ import annotations

import io
import json
import os
import textwrap
import uuid
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ---------------------------------------------------------------------------
# Database URL — prefer env var so CI can inject Postgres
# ---------------------------------------------------------------------------
_DEFAULT_TEST_DB = "sqlite+aiosqlite:///:memory:"
TEST_DATABASE_URL: str = os.environ.get("TEST_DATABASE_URL", _DEFAULT_TEST_DB)
_IS_SQLITE = "sqlite" in TEST_DATABASE_URL

# ---------------------------------------------------------------------------
# Session-scoped engine + schema bootstrap
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def async_engine():
    """Session-scoped engine. Creates schema once, drops after the full run."""
    connect_args: dict[str, Any] = {"check_same_thread": False} if _IS_SQLITE else {}
    engine = create_async_engine(TEST_DATABASE_URL, echo=False, connect_args=connect_args)

    from finspark.models.base import Base  # noqa: PLC0415

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def session_factory(async_engine):
    return async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


# ---------------------------------------------------------------------------
# Per-test transaction isolation
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def db_connection(async_engine) -> AsyncGenerator[AsyncConnection, None]:
    """Wraps each test in a transaction that is rolled back at teardown."""
    async with async_engine.connect() as conn:
        await conn.begin()
        yield conn
        await conn.rollback()


@pytest_asyncio.fixture()
async def db_session(db_connection: AsyncConnection) -> AsyncGenerator[AsyncSession, None]:
    session = AsyncSession(bind=db_connection, expire_on_commit=False)
    yield session
    await session.close()


# ---------------------------------------------------------------------------
# FastAPI app + async HTTP client
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def app_instance():
    from finspark.main import app  # noqa: PLC0415

    return app


@pytest_asyncio.fixture()
async def client(
    app_instance,
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    """
    Async HTTP client wired to the ASGI app.
    Overrides get_db so every route shares the rolled-back test session.
    """
    from finspark.core.db import get_db  # noqa: PLC0415

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app_instance.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app_instance)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"Content-Type": "application/json"},
    ) as ac:
        yield ac

    app_instance.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_token(mock_tenant: dict[str, Any]) -> str:
    """Fake bearer token. Swap for a real JWT in integration tests."""
    return f"test-token-{mock_tenant['id']}"


@pytest.fixture()
def tenant_headers(mock_tenant: dict[str, Any], auth_token: str) -> dict[str, str]:
    return {
        "X-Tenant-ID": mock_tenant["id"],
        "X-Tenant-Slug": mock_tenant["slug"],
        "Authorization": f"Bearer {auth_token}",
    }


# ---------------------------------------------------------------------------
# Tenant fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_tenant() -> dict[str, Any]:
    """Returns a plain-dict tenant representation for use in tests."""
    tenant_id = str(uuid.uuid4())
    return {
        "id": tenant_id,
        "name": "Test Corp",
        "slug": f"test-corp-{tenant_id[:8]}",
        "plan": "enterprise",
        "is_active": True,
    }


@pytest.fixture()
def other_tenant() -> dict[str, Any]:
    """A second independent tenant for cross-tenant isolation tests."""
    tenant_id = str(uuid.uuid4())
    return {
        "id": tenant_id,
        "name": "Rival Corp",
        "slug": f"rival-corp-{tenant_id[:8]}",
        "plan": "standard",
        "is_active": True,
    }


# ---------------------------------------------------------------------------
# Sample documents
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_brd_text() -> str:
    return textwrap.dedent(
        """
        Business Requirements Document - Customer Onboarding Integration
        Version: 2.1  |  Date: 2026-01-15

        1. SCOPE
        The onboarding flow must integrate with:
          - CIBIL credit bureau (v2.0 API) for credit score lookup
          - Aadhaar eKYC via UIDAI API for identity verification
          - GSTN for GST verification using PAN
          - Setu Account Aggregator API v1.1 for bank statement fetch
          - Razorpay Payment Gateway for payment collection (v1, fallback v2)

        2. FIELD MAPPINGS
        Customer PAN  -> bureau.pan_number (string, required)
        Customer DOB  -> kyc.date_of_birth (ISO-8601 date)
        Mobile Number -> kyc.mobile (10-digit E.164)
        Annual Income -> bureau.annual_income (float, INR)

        3. SLA
        Bureau calls: 3 s timeout, 3 retries with exponential backoff
        KYC calls: 5 s timeout, 2 retries

        4. SECURITY
        All credentials stored in Vault. PAN must be masked in logs.
        """
    )


@pytest.fixture()
def sample_api_spec() -> dict[str, Any]:
    return {
        "openapi": "3.0.3",
        "info": {"title": "CIBIL Bureau API", "version": "2.0.0"},
        "paths": {
            "/score": {
                "post": {
                    "operationId": "getScore",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["pan_number", "date_of_birth"],
                                    "properties": {
                                        "pan_number": {
                                            "type": "string",
                                            "pattern": "^[A-Z]{5}[0-9]{4}[A-Z]$",
                                        },
                                        "date_of_birth": {
                                            "type": "string",
                                            "format": "date",
                                        },
                                        "annual_income": {"type": "number"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Credit score",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "score": {
                                                "type": "integer",
                                                "minimum": 300,
                                                "maximum": 900,
                                            },
                                            "report_id": {"type": "string"},
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }


@pytest.fixture()
def sample_pdf_bytes() -> bytes:
    """Minimal valid single-page PDF - no external files needed."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length 52>>\nstream\n"
        b"BT /F1 12 Tf 72 720 Td (Integration BRD) Tj ET\n"
        b"endstream\nendobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000266 00000 n \n"
        b"0000000370 00000 n \n"
        b"trailer<</Size 6/Root 1 0 R>>\n"
        b"startxref\n441\n%%EOF"
    )


@pytest.fixture()
def sample_pdf_file(sample_pdf_bytes: bytes) -> io.BytesIO:
    buf = io.BytesIO(sample_pdf_bytes)
    buf.name = "brd.pdf"
    return buf


@pytest.fixture()
def sample_docx_bytes() -> bytes:
    """Minimal valid DOCX (ZIP container) - no python-docx dependency at import."""
    import zipfile  # noqa: PLC0415

    buf = io.BytesIO()
    ct = (
        '<?xml version="1.0"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels"'
        ' ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Override PartName="/word/document.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument'
        ".wordprocessingml.document.main+xml\"/>"
        "</Types>"
    )
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
        ' Target="word/document.xml"/>'
        "</Relationships>"
    )
    doc = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>Integrate with CIBIL bureau API v2.0</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Customer PAN maps to bureau.pan_number (required)</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>KYC via UIDAI API timeout 5s retries 2</w:t></w:r></w:p>"
        "</w:body>"
        "</w:document>"
    )
    doc_rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        "</Relationships>"
    )
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("word/document.xml", doc)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
    return buf.getvalue()


@pytest.fixture()
def sample_docx_file(sample_docx_bytes: bytes) -> io.BytesIO:
    buf = io.BytesIO(sample_docx_bytes)
    buf.name = "brd.docx"
    return buf


# ---------------------------------------------------------------------------
# Upload helpers (httpx multipart tuples)
# ---------------------------------------------------------------------------


@pytest.fixture()
def upload_pdf(sample_pdf_bytes: bytes) -> tuple[str, io.BytesIO, str]:
    return ("brd.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")


@pytest.fixture()
def upload_docx(sample_docx_bytes: bytes) -> tuple[str, io.BytesIO, str]:
    return (
        "brd.docx",
        io.BytesIO(sample_docx_bytes),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ---------------------------------------------------------------------------
# LLM mock
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_llm_response() -> dict[str, Any]:
    return {
        "adapters": [
            {
                "id": "cibil-bureau",
                "version": "2.0",
                "endpoints": ["/score"],
                "timeout_ms": 3000,
                "retry_count": 3,
            },
            {
                "id": "uidai-kyc",
                "version": "1.0",
                "endpoints": ["/verify"],
                "timeout_ms": 5000,
                "retry_count": 2,
            },
        ],
        "field_mappings": [
            {"source": "customer.pan", "target": "bureau.pan_number", "transform": None},
            {
                "source": "customer.dob",
                "target": "kyc.date_of_birth",
                "transform": "iso8601_date",
            },
        ],
        "config_diff": [],
        "confidence": 0.92,
    }


@pytest.fixture()
def mock_gemini(mock_llm_response: dict[str, Any]):
    """
    Patches GeminiClient methods. Yields the mock client for assertion in tests.
    """
    mock_client = AsyncMock()
    mock_client.generate = AsyncMock(return_value=json.dumps(mock_llm_response))
    mock_client.generate_json = AsyncMock(return_value=mock_llm_response)
    mock_client.api_key = "test-key"
    mock_client.model = "gemini-2.5-flash"

    with patch(
        "finspark.services.llm.client.get_llm_client",
        return_value=mock_client,
    ):
        yield mock_client


@pytest.fixture()
def mock_openai(mock_llm_response: dict[str, Any]):
    """
    Legacy: returns a mock async client shaped like openai.AsyncOpenAI.
    Does not require the openai package to be installed.
    """
    completion = MagicMock()
    completion.choices = [
        MagicMock(
            message=MagicMock(
                content=json.dumps(mock_llm_response),
                tool_calls=None,
            )
        )
    ]
    completion.usage = MagicMock(prompt_tokens=120, completion_tokens=80, total_tokens=200)

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=completion)
    yield mock_client


# ---------------------------------------------------------------------------
# External API response stubs
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_bureau_responses() -> dict[str, Any]:
    return {
        "success": {"score": 750, "report_id": "RPT-2026-001"},
        "low_score": {"score": 350, "report_id": "RPT-2026-002"},
        "error_400": {"error": "invalid_pan", "message": "PAN format invalid"},
        "error_503": {"error": "service_unavailable", "retry_after": 30},
    }


@pytest.fixture()
def mock_kyc_responses() -> dict[str, Any]:
    return {
        "success": {
            "verified": True,
            "aadhaar_masked": "XXXX-XXXX-4567",
            "name_match_score": 0.97,
        },
        "mismatch": {
            "verified": False,
            "aadhaar_masked": "XXXX-XXXX-4567",
            "name_match_score": 0.41,
        },
    }


# ---------------------------------------------------------------------------
# Domain payload fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def adapter_payload() -> dict[str, Any]:
    return {
        "name": "CIBIL Bureau",
        "slug": "cibil-bureau",
        "category": "credit_bureau",
        "versions": ["1.0", "2.0"],
        "latest_version": "2.0",
        "is_active": True,
    }


@pytest.fixture()
def integration_payload(mock_tenant: dict[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": mock_tenant["id"],
        "adapter_slug": "cibil-bureau",
        "adapter_version": "2.0",
        "name": "Credit Check",
        "config": {
            "base_url": "https://api.cibil.example.com",
            "timeout_ms": 3000,
            "retry_count": 3,
            "auth": {"type": "api_key", "header": "X-API-Key"},
        },
        "is_active": True,
    }


@pytest.fixture()
def field_mapping_payload(mock_tenant: dict[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": mock_tenant["id"],
        "adapter_slug": "cibil-bureau",
        "source_field": "customer.pan",
        "target_field": "bureau.pan_number",
        "transform": None,
        "is_required": True,
    }
