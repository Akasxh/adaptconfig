"""Tests for security headers middleware and CORS origin restrictions."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from finspark.main import SecurityHeadersMiddleware


def _make_app(debug: bool = True) -> FastAPI:
    """Build a minimal FastAPI app with SecurityHeadersMiddleware."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, debug=debug)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest_asyncio.fixture()
async def debug_client() -> AsyncClient:
    app = _make_app(debug=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


@pytest_asyncio.fixture()
async def prod_client() -> AsyncClient:
    app = _make_app(debug=False)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_x_content_type_options_present(debug_client: AsyncClient) -> None:
    response = await debug_client.get("/ping")
    assert response.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.asyncio
async def test_x_frame_options_present(debug_client: AsyncClient) -> None:
    response = await debug_client.get("/ping")
    assert response.headers.get("x-frame-options") == "DENY"


@pytest.mark.asyncio
async def test_x_xss_protection_present(debug_client: AsyncClient) -> None:
    response = await debug_client.get("/ping")
    assert response.headers.get("x-xss-protection") == "1; mode=block"


@pytest.mark.asyncio
async def test_referrer_policy_present(debug_client: AsyncClient) -> None:
    response = await debug_client.get("/ping")
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_hsts_absent_in_debug(debug_client: AsyncClient) -> None:
    response = await debug_client.get("/ping")
    assert "strict-transport-security" not in response.headers


@pytest.mark.asyncio
async def test_hsts_present_in_production(prod_client: AsyncClient) -> None:
    response = await prod_client.get("/ping")
    hsts = response.headers.get("strict-transport-security", "")
    assert "max-age=31536000" in hsts
    assert "includeSubDomains" in hsts


# ---------------------------------------------------------------------------
# CORS origin restrictions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cors_allows_known_origin() -> None:
    """Allowed origin receives Access-Control-Allow-Origin header."""
    from finspark.core.config import Settings

    patched = Settings(
        APP_DEBUG=True,
        ALLOWED_ORIGINS=["http://localhost:3000"],
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
    )

    with patch("finspark.main.settings", patched):
        from finspark.main import create_app

        app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": "http://localhost:3000"},
    ) as client:
        response = await client.options(
            "/api",
            headers={
                "Access-Control-Request-Method": "GET",
                "Origin": "http://localhost:3000",
            },
        )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


@pytest.mark.asyncio
async def test_cors_rejects_unknown_origin() -> None:
    """Unknown origin should not receive Access-Control-Allow-Origin header."""
    from finspark.core.config import Settings

    patched = Settings(
        APP_DEBUG=True,
        ALLOWED_ORIGINS=["http://localhost:3000"],
        DATABASE_URL="sqlite+aiosqlite:///:memory:",
    )

    with patch("finspark.main.settings", patched):
        from finspark.main import create_app

        app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": "https://evil.example.com"},
    ) as client:
        response = await client.options(
            "/api",
            headers={
                "Access-Control-Request-Method": "GET",
                "Origin": "https://evil.example.com",
            },
        )
    assert response.headers.get("access-control-allow-origin") != "https://evil.example.com"


def test_wildcard_origins_rejected_in_production() -> None:
    """Settings must raise when ALLOWED_ORIGINS=['*'] and APP_DEBUG=False."""
    from pydantic import ValidationError

    from finspark.core.config import Settings

    with pytest.raises(ValidationError, match="must not contain"):
        Settings(
            APP_DEBUG=False,
            ALLOWED_ORIGINS=["*"],
            DATABASE_URL="sqlite+aiosqlite:///:memory:",
        )
