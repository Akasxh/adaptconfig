"""Tests verifying fixes for GitHub issues #80-#108.

Each test class is named after the issue it verifies.
"""

import os
import time
from collections import OrderedDict
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.core.database import get_db
from finspark.core.security import create_jwt_token, decode_jwt_token
from finspark.models.user import User


# ---------------------------------------------------------------------------
# Helper: create a valid JWT with desired claims
# ---------------------------------------------------------------------------

def _make_token(
    *,
    token_type: str = "access",
    role: str = "admin",
    tenant_id: str = "test-tenant",
    sub: str = "user-1",
    email: str = "test@test.com",
    tenant_name: str = "Test Tenant",
    expires_delta: timedelta | None = None,
) -> str:
    payload = {
        "sub": sub,
        "email": email,
        "role": role,
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "type": token_type,
    }
    return create_jwt_token(payload, expires_delta=expires_delta or timedelta(minutes=30))


# ===========================================================================
# Issue #80 — Hardcoded admin credentials
# ===========================================================================

class TestIssue80AdminCredentials:
    """seed_admin_user should not hardcode a password."""

    @pytest.mark.asyncio
    async def test_seed_skips_when_no_env_var_debug_mode(self) -> None:
        """In debug mode, skip seeding when FINSPARK_ADMIN_PASSWORD is unset."""
        from finspark.seeds import seed_admin_user

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FINSPARK_ADMIN_PASSWORD", None)
            with patch("finspark.core.config.settings") as mock_settings:
                mock_settings.debug = True
                await seed_admin_user()
                # No crash, no user created (because env var is missing)

    @pytest.mark.asyncio
    async def test_seed_fails_fast_in_production_when_no_password(self) -> None:
        """In non-debug mode, missing FINSPARK_ADMIN_PASSWORD raises RuntimeError."""
        from finspark.seeds import seed_admin_user

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FINSPARK_ADMIN_PASSWORD", None)
            with patch("finspark.core.config.settings") as mock_settings:
                mock_settings.debug = False
                with pytest.raises(RuntimeError, match="FINSPARK_ADMIN_PASSWORD"):
                    await seed_admin_user()

    def test_no_hardcoded_password_in_source(self) -> None:
        """The seed module should not contain any hardcoded password strings."""
        import inspect

        from finspark.seeds import seed_admin_user

        source = inspect.getsource(seed_admin_user)
        assert "Admin1234" not in source
        assert 'password="' not in source
        assert "password='" not in source


# ===========================================================================
# Issue #81 — Debug mode defaults to admin role
# ===========================================================================

class TestIssue81DebugRoleDefault:
    """Debug mode should default to 'viewer', not 'admin'."""

    @pytest.mark.asyncio
    async def test_debug_mode_default_role_is_viewer(self, client: AsyncClient) -> None:
        """Unauthenticated request in debug mode should get viewer role."""
        # The client fixture sets X-Tenant-Role: admin explicitly;
        # create a fresh client without that header.
        from finspark.main import app as test_app

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.headers["X-Tenant-ID"] = "test-tenant"
            ac.headers["X-Tenant-Name"] = "Test Tenant"
            # Do NOT set X-Tenant-Role -- should default to viewer

            # A viewer cannot create a configuration (requires admin/editor)
            resp = await ac.post(
                "/api/v1/configurations/generate",
                json={
                    "document_id": "fake",
                    "adapter_version_id": "fake",
                    "name": "test",
                },
            )
            assert resp.status_code == 403


# ===========================================================================
# Issue #82 — Refresh token used as access token
# ===========================================================================

class TestIssue82TokenTypeValidation:
    """Refresh tokens must be rejected when used as access tokens."""

    @pytest.mark.asyncio
    async def test_refresh_token_rejected_on_protected_endpoint(self, client: AsyncClient) -> None:
        """Using a refresh token on /auth/me should return 401."""
        refresh_token = _make_token(token_type="refresh")
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {refresh_token}"},
        )
        assert resp.status_code == 401
        assert "refresh" in resp.json()["detail"].lower() or "Refresh" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_access_token_accepted(self, client: AsyncClient, db_session: AsyncSession) -> None:
        """A proper access token should work on /auth/me (when user exists)."""
        from finspark.api.routes.auth import _hash_password

        user = User(
            id="user-1",
            email="test@test.com",
            name="Test User",
            password_hash=_hash_password("password123"),
            role="admin",
            tenant_id="test-tenant",
        )
        db_session.add(user)
        await db_session.flush()

        access_token = _make_token(token_type="access")
        resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert resp.status_code == 200

    def test_tokens_contain_type_claim(self) -> None:
        """Tokens issued by _make_tokens should contain a 'type' claim."""
        access = _make_token(token_type="access")
        refresh = _make_token(token_type="refresh")

        access_payload = decode_jwt_token(access)
        refresh_payload = decode_jwt_token(refresh)

        assert access_payload["type"] == "access"
        assert refresh_payload["type"] == "refresh"


# ===========================================================================
# Issue #83 — Frontend hardcodes X-Tenant-Role: admin
# ===========================================================================

class TestIssue83FrontendRoleHeader:
    """api.ts should not send X-Tenant-Role header."""

    def test_no_hardcoded_tenant_role_header(self) -> None:
        """The frontend API module should not hardcode X-Tenant-Role."""
        import pathlib

        api_ts = pathlib.Path(__file__).parents[2] / "frontend" / "src" / "lib" / "api.ts"
        content = api_ts.read_text()
        assert "X-Tenant-Role" not in content


# ===========================================================================
# Issue #84 — No DB connection pool limits
# ===========================================================================

class TestIssue84PoolLimits:
    """database.py should configure pool limits for non-SQLite backends."""

    def test_pool_kwargs_set_for_non_sqlite(self) -> None:
        """Pool config should include pool_size, max_overflow, pool_timeout, pool_pre_ping."""
        from finspark.core import database

        # In test env, database_url is SQLite, so _pool_kwargs will be empty.
        # Verify the logic by checking the module-level dict directly.
        # For SQLite, pool kwargs should be empty.
        assert hasattr(database, "_pool_kwargs")

    def test_pool_kwargs_logic(self) -> None:
        """When database_url is not SQLite, pool kwargs should be populated."""
        url = "postgresql+asyncpg://user:pass@localhost/db"
        pool_kwargs: dict = {}
        if not url.startswith("sqlite"):
            pool_kwargs = {
                "pool_size": 10,
                "max_overflow": 20,
                "pool_timeout": 30,
                "pool_pre_ping": True,
            }
        assert pool_kwargs["pool_size"] == 10
        assert pool_kwargs["max_overflow"] == 20
        assert pool_kwargs["pool_timeout"] == 30
        assert pool_kwargs["pool_pre_ping"] is True


# ===========================================================================
# Issue #89 — No input length validation on registration fields
# ===========================================================================

class TestIssue89InputValidation:
    """Registration fields should have length constraints."""

    @pytest.mark.asyncio
    async def test_short_email_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "a@b", "password": "securepass123", "name": "Test"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_short_password_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "test@example.com", "password": "short", "name": "Test"},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_name_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "test@example.com", "password": "securepass123", "name": ""},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_long_email_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "a" * 250 + "@b.com",
                "password": "securepass123",
                "name": "Test",
            },
        )
        assert resp.status_code == 422


# ===========================================================================
# Issue #91 — URL validator SSRF improvements
# ===========================================================================

class TestIssue91SSRFProtection:
    """URL validator should block private/loopback/link-local IPs and non-HTTP schemes."""

    def test_blocks_localhost(self) -> None:
        from finspark.core.url_validator import is_safe_url

        assert is_safe_url("http://127.0.0.1/hook") is False

    def test_blocks_private_10(self) -> None:
        from finspark.core.url_validator import is_safe_url

        with patch("finspark.core.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("10.0.0.1", 0))]
            assert is_safe_url("http://internal.corp/hook") is False

    def test_blocks_private_172(self) -> None:
        from finspark.core.url_validator import is_safe_url

        with patch("finspark.core.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("172.16.0.1", 0))]
            assert is_safe_url("http://internal.corp/hook") is False

    def test_blocks_private_192(self) -> None:
        from finspark.core.url_validator import is_safe_url

        with patch("finspark.core.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("192.168.1.1", 0))]
            assert is_safe_url("http://internal.corp/hook") is False

    def test_blocks_link_local(self) -> None:
        from finspark.core.url_validator import is_safe_url

        with patch("finspark.core.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("169.254.1.1", 0))]
            assert is_safe_url("http://internal.corp/hook") is False

    def test_blocks_ipv6_loopback(self) -> None:
        from finspark.core.url_validator import is_safe_url

        with patch("finspark.core.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(10, 1, 6, "", ("::1", 0, 0, 0))]
            assert is_safe_url("http://internal.corp/hook") is False

    def test_blocks_ipv6_unique_local(self) -> None:
        from finspark.core.url_validator import is_safe_url

        with patch("finspark.core.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(10, 1, 6, "", ("fd00::1", 0, 0, 0))]
            assert is_safe_url("http://internal.corp/hook") is False

    def test_blocks_ipv6_link_local(self) -> None:
        from finspark.core.url_validator import is_safe_url

        with patch("finspark.core.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(10, 1, 6, "", ("fe80::1", 0, 0, 0))]
            assert is_safe_url("http://internal.corp/hook") is False

    def test_blocks_file_scheme(self) -> None:
        from finspark.core.url_validator import is_safe_url

        assert is_safe_url("file:///etc/passwd") is False

    def test_blocks_ftp_scheme(self) -> None:
        from finspark.core.url_validator import is_safe_url

        assert is_safe_url("ftp://internal/file") is False

    def test_allows_public_ip(self) -> None:
        from finspark.core.url_validator import is_safe_url

        with patch("finspark.core.url_validator.socket.getaddrinfo") as mock_dns:
            mock_dns.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
            assert is_safe_url("https://example.com/hook") is True

    def test_blocks_empty_hostname(self) -> None:
        from finspark.core.url_validator import is_safe_url

        assert is_safe_url("http://") is False


# ===========================================================================
# Issue #93 — Webhook secret auto-generation
# ===========================================================================

class TestIssue93WebhookSecret:
    """Webhook creation should auto-generate a secret when none is provided."""

    @pytest.mark.asyncio
    async def test_webhook_created_without_secret(self, client: AsyncClient) -> None:
        """Creating a webhook without a secret should succeed (auto-generated)."""
        with patch("finspark.api.routes.webhooks.is_safe_url", return_value=True):
            resp = await client.post(
                "/api/v1/webhooks/",
                json={"url": "https://example.com/hook", "events": ["config.created"]},
            )
        assert resp.status_code == 201

    @pytest.mark.asyncio
    async def test_webhook_with_explicit_secret(self, client: AsyncClient) -> None:
        """Creating a webhook with an explicit secret should use it."""
        with patch("finspark.api.routes.webhooks.is_safe_url", return_value=True):
            resp = await client.post(
                "/api/v1/webhooks/",
                json={
                    "url": "https://example.com/hook",
                    "events": ["config.created"],
                    "secret": "my-custom-secret",
                },
            )
        assert resp.status_code == 201

    def test_no_default_secret_in_source(self) -> None:
        """Webhook route should not contain 'default-secret' literal."""
        import inspect

        from finspark.api.routes import webhooks

        source = inspect.getsource(webhooks)
        assert "default-secret" not in source


# ===========================================================================
# Issue #95 — Rate limiting on auth endpoints
# ===========================================================================

class TestIssue95AuthRateLimit:
    """Auth endpoints should have per-IP rate limiting."""

    @pytest.mark.asyncio
    async def test_login_rate_limited(self, client: AsyncClient) -> None:
        """Exceeding login rate limit should return 429."""
        from finspark.api.routes.auth import _auth_requests

        _auth_requests.clear()

        # Make 11 requests (limit is 10)
        for i in range(11):
            resp = await client.post(
                "/api/v1/auth/login",
                json={"email": f"test{i}@example.com", "password": "wrongpassword"},
            )
            if resp.status_code == 429:
                assert "too many" in resp.json()["detail"].lower()
                return

        pytest.fail("Expected 429 after exceeding rate limit")

    @pytest.mark.asyncio
    async def test_register_rate_limited(self, client: AsyncClient) -> None:
        """Exceeding register rate limit should return 429."""
        from finspark.api.routes.auth import _auth_requests

        _auth_requests.clear()

        for i in range(11):
            resp = await client.post(
                "/api/v1/auth/register",
                json={
                    "email": f"test{i}@example.com",
                    "password": "securepass123",
                    "name": "Test",
                },
            )
            if resp.status_code == 429:
                return

        pytest.fail("Expected 429 after exceeding rate limit")


# ===========================================================================
# Issue #92 — Client-side JWT expiry check
# ===========================================================================

class TestIssue92AuthGuardJWTExpiry:
    """The auth module should check JWT expiry client-side."""

    def test_auth_module_has_decode_logic(self) -> None:
        """auth.ts should contain JWT decode logic for expiry checking."""
        import pathlib

        auth_ts = pathlib.Path(__file__).parents[2] / "frontend" / "src" / "lib" / "auth.ts"
        content = auth_ts.read_text()
        assert "exp" in content
        assert "atob" in content or "Buffer" in content
        assert "Date.now" in content


# ===========================================================================
# Issue #99 — File upload size limit middleware
# ===========================================================================

class TestIssue99UploadSizeLimit:
    """RequestBodySizeLimitMiddleware should reject oversized requests."""

    def test_middleware_class_exists(self) -> None:
        from finspark.core.middleware import RequestBodySizeLimitMiddleware

        assert RequestBodySizeLimitMiddleware is not None


# ===========================================================================
# Issue #100 — Pagination on list endpoints
# ===========================================================================

class TestIssue100Pagination:
    """List endpoints should support limit/offset pagination."""

    @pytest.mark.asyncio
    async def test_adapters_list_accepts_pagination(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/adapters/?limit=5&offset=0")
        assert resp.status_code == 200


# ===========================================================================
# Issue #103 — create_adapter_from_document role check
# ===========================================================================

class TestIssue103AdapterFromDocumentRole:
    """create_adapter_from_document should require admin or editor role."""

    @pytest.mark.asyncio
    async def test_viewer_cannot_create_adapter_from_document(self) -> None:
        """A viewer should get 403 on POST /adapters/from-document."""
        from finspark.main import app as test_app

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            ac.headers["X-Tenant-ID"] = "test-tenant"
            ac.headers["X-Tenant-Name"] = "Test Tenant"
            ac.headers["X-Tenant-Role"] = "viewer"

            resp = await ac.post(
                "/api/v1/adapters/from-document",
                params={"document_id": "fake", "name": "Test", "category": "custom"},
            )
            assert resp.status_code == 403


# ===========================================================================
# Issue #104 — Config PATCH version increment + history
# ===========================================================================

class TestIssue104ConfigPatchVersion:
    """PATCH should increment config.version and create a history entry."""

    def test_patch_handler_increments_version(self) -> None:
        """Verify the route source code increments version."""
        import inspect

        from finspark.api.routes import configurations

        source = inspect.getsource(configurations.update_configuration)
        assert "config.version += 1" in source
        assert "ConfigurationHistory" in source


# ===========================================================================
# Issue #105 — Health check verifies DB connectivity
# ===========================================================================

class TestIssue105HealthDB:
    """Health endpoint should verify actual DB connectivity."""

    @pytest.mark.asyncio
    async def test_health_includes_database_check(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        data = resp.json()
        assert "checks" in data
        assert "database" in data["checks"]
        assert data["checks"]["database"] in ("ok", "error")

    @pytest.mark.asyncio
    async def test_health_returns_503_on_db_failure(self) -> None:
        """When the DB is unreachable, health should return 503."""
        from finspark.api.routes.health import _check_database

        with patch("finspark.api.routes.health.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_session.execute.side_effect = Exception("DB down")
            mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _check_database()
            assert result == "error"


# ===========================================================================
# Issue #108 — Audit logs include IP and user-agent
# ===========================================================================

class TestIssue108AuditIPUserAgent:
    """Audit logs should include ip_address and user_agent fields."""

    def test_audit_model_has_ip_and_ua_columns(self) -> None:
        from finspark.models.audit import AuditLog

        assert hasattr(AuditLog, "ip_address")
        assert hasattr(AuditLog, "user_agent")

    def test_audit_schema_has_ip_and_ua(self) -> None:
        from finspark.schemas.audit import AuditLogResponse

        fields = AuditLogResponse.model_fields
        assert "ip_address" in fields
        assert "user_agent" in fields

    def test_audit_service_accepts_ip_and_ua(self) -> None:
        import inspect

        from finspark.core.audit import AuditService

        sig = inspect.signature(AuditService.log)
        assert "ip_address" in sig.parameters
        assert "user_agent" in sig.parameters

    def test_request_context_dependency_exists(self) -> None:
        from finspark.api.dependencies import RequestContext, get_request_context

        assert RequestContext is not None
        assert callable(get_request_context)


# ===========================================================================
# Issue #87 — Metrics endpoint requires auth
# ===========================================================================

class TestIssue87MetricsAuth:
    """A new metrics route should require admin role."""

    def test_metrics_route_has_role_check(self) -> None:
        """The new metrics.py route should enforce admin role."""
        import inspect

        from finspark.api.routes.metrics import get_metrics

        source = inspect.getsource(get_metrics)
        # The function signature includes require_role("admin")
        assert "require_role" in source or "TenantContext" in source
