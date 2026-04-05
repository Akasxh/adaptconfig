"""Tests for security headers middleware (Issue #53) and trusted host config (Issue #67)."""

import pytest
from httpx import AsyncClient

from finspark.core.config import Settings


EXPECTED_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    ),
}


class TestSecurityHeadersMiddleware:
    @pytest.mark.asyncio
    async def test_health_endpoint_has_security_headers(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        for header_name, header_value in EXPECTED_SECURITY_HEADERS.items():
            assert header_name in response.headers, f"Missing header: {header_name}"
            assert response.headers[header_name] == header_value

    @pytest.mark.asyncio
    async def test_api_endpoint_has_security_headers(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/adapters/")
        for header_name, header_value in EXPECTED_SECURITY_HEADERS.items():
            assert header_name in response.headers, f"Missing header: {header_name}"
            assert response.headers[header_name] == header_value

    @pytest.mark.asyncio
    async def test_404_has_security_headers(self, client: AsyncClient) -> None:
        response = await client.get("/nonexistent")
        assert response.status_code in (404, 405)
        for header_name in EXPECTED_SECURITY_HEADERS:
            assert header_name in response.headers, f"Missing header on error: {header_name}"


class TestTrustedHostConfig:
    def test_default_allowed_hosts(self) -> None:
        s = Settings(debug=True)
        assert s.allowed_hosts == ["*"]

    def test_allowed_hosts_configurable(self) -> None:
        s = Settings(debug=True, allowed_hosts=["example.com", "api.example.com"])
        assert "example.com" in s.allowed_hosts
        assert "api.example.com" in s.allowed_hosts
