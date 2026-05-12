"""Tests for JWT-based authentication in TenantMiddleware."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from finspark.core.middleware import DEFAULT_TENANT_ID, DEFAULT_TENANT_NAME, TenantMiddleware
from finspark.core.security import create_tenant_token


def _make_app(path: str = "/") -> Starlette:
    """Minimal Starlette app with TenantMiddleware for auth testing."""

    async def endpoint(request: Request) -> PlainTextResponse:
        tenant_id = getattr(request.state, "tenant_id", "NOT_SET")
        tenant_name = getattr(request.state, "tenant_name", "NOT_SET")
        role = getattr(request.state, "role", "NOT_SET")
        return PlainTextResponse(f"{tenant_id}|{tenant_name}|{role}")

    app = Starlette(routes=[Route(path, endpoint)])
    app.add_middleware(TenantMiddleware)
    return app


# ---------------------------------------------------------------------------
# Production mode – JWT required
# ---------------------------------------------------------------------------


class TestProductionModeJWT:
    """TenantMiddleware in production mode (debug=False) requires a valid JWT."""

    @pytest.fixture(autouse=True)
    def production_mode(self) -> Any:
        with patch("finspark.core.middleware.settings") as mock_settings:
            mock_settings.debug = False
            yield mock_settings

    def test_missing_auth_header_returns_401(self) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/")
        assert resp.status_code == 401

    def test_non_bearer_auth_header_returns_401(self) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/", headers={"Authorization": "Basic dXNlcjpwYXNz"})
        assert resp.status_code == 401

    def test_invalid_jwt_returns_401(self) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401

    def test_valid_jwt_grants_correct_tenant_context(self) -> None:
        token = create_tenant_token("acme-corp", "Acme Corp", "admin")
        client = TestClient(_make_app(), raise_server_exceptions=True)
        resp = client.get("/", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert "acme-corp" in resp.text
        assert "Acme Corp" in resp.text
        assert "admin" in resp.text

    def test_valid_jwt_extracts_role(self) -> None:
        token = create_tenant_token("t1", "Tenant One", "admin")
        client = TestClient(_make_app(), raise_server_exceptions=True)
        resp = client.get("/", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert "admin" in resp.text

    def test_valid_jwt_sets_response_tenant_id_header(self) -> None:
        token = create_tenant_token("hdr-tenant", "HDR", "admin")
        client = TestClient(_make_app(), raise_server_exceptions=True)
        resp = client.get("/", headers={"Authorization": f"Bearer {token}"})
        assert resp.headers.get("x-tenant-id") == "hdr-tenant"

    def test_401_response_has_detail_key(self) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=False)
        resp = client.get("/")
        assert "detail" in resp.json()


# ---------------------------------------------------------------------------
# Development mode – header-based auth with safe defaults
# ---------------------------------------------------------------------------


class TestDevelopmentModeHeaders:
    """TenantMiddleware in dev mode (debug=True) uses X-Tenant-* headers."""

    @pytest.fixture(autouse=True)
    def dev_mode(self) -> Any:
        with patch("finspark.core.middleware.settings") as mock_settings:
            mock_settings.debug = True
            yield mock_settings

    def test_header_based_auth_allowed(self) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=True)
        resp = client.get(
            "/",
            headers={"X-Tenant-ID": "dev-tenant", "X-Tenant-Name": "Dev Tenant"},
        )
        assert resp.status_code == 200
        assert "dev-tenant" in resp.text

    def test_missing_headers_use_safe_defaults(self) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=True)
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.text
        assert DEFAULT_TENANT_ID in body
        assert DEFAULT_TENANT_NAME in body

    def test_default_role_is_admin_in_dev(self) -> None:
        """Dev mode defaults to admin for convenience during development."""
        client = TestClient(_make_app(), raise_server_exceptions=True)
        resp = client.get("/", headers={"X-Tenant-ID": "t1"})
        assert resp.status_code == 200
        assert "admin" in resp.text

    def test_explicit_role_header_is_respected(self) -> None:
        client = TestClient(_make_app(), raise_server_exceptions=True)
        resp = client.get(
            "/",
            headers={"X-Tenant-ID": "t1", "X-Tenant-Role": "configurator"},
        )
        assert "configurator" in resp.text

    def test_no_auth_header_required(self) -> None:
        """Dev mode must not require Authorization header."""
        client = TestClient(_make_app(), raise_server_exceptions=True)
        resp = client.get("/")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth bypass paths – always skip auth regardless of mode
# ---------------------------------------------------------------------------


class TestAuthBypassPaths:
    """Health/docs endpoints bypass authentication in both production and dev modes."""

    BYPASS_PATHS = ["/health", "/docs", "/redoc", "/openapi.json", "/metrics"]

    @pytest.fixture(autouse=True)
    def production_mode(self) -> Any:
        with patch("finspark.core.middleware.settings") as mock_settings:
            mock_settings.debug = False
            yield mock_settings

    @pytest.mark.parametrize("path", BYPASS_PATHS)
    def test_bypass_path_returns_200_without_token(self, path: str) -> None:
        """Bypass paths must not return 401, even in production mode without a token."""

        async def handler(request: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route(path, handler)])
        app.add_middleware(TenantMiddleware)
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get(path)
        assert resp.status_code == 200

    @pytest.mark.parametrize("path", BYPASS_PATHS)
    def test_bypass_path_returns_200_in_dev_mode(self, path: str) -> None:
        with patch("finspark.core.middleware.settings") as mock_settings:
            mock_settings.debug = True

            async def handler(request: Request) -> PlainTextResponse:
                return PlainTextResponse("ok")

            app = Starlette(routes=[Route(path, handler)])
            app.add_middleware(TenantMiddleware)
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get(path)
            assert resp.status_code == 200
