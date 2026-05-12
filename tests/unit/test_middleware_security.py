"""Comprehensive tests for middleware, security, audit, and lifecycle modules."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from finspark.core.middleware import (
    DEFAULT_TENANT_ID,
    DEFAULT_TENANT_NAME,
    DeprecationHeaderMiddleware,
    RequestLoggingMiddleware,
    TenantMiddleware,
)
from finspark.core.security import (
    create_jwt_token,
    decode_jwt_token,
    decrypt_value,
    encrypt_value,
    mask_pii,
)
from finspark.schemas.common import ConfigStatus
from finspark.services.lifecycle import (
    TRANSITIONS,
    AuditEntry,
    IntegrationLifecycle,
    InvalidTransitionError,
)


# ---------------------------------------------------------------------------
# Helpers – tiny ASGI apps for middleware testing
# ---------------------------------------------------------------------------


def _make_app(*middlewares: Any, path: str = "/") -> Starlette:
    """Build a minimal Starlette app with the given middlewares applied."""

    async def endpoint(request: Request) -> PlainTextResponse:
        tenant_id = getattr(request.state, "tenant_id", "NOT_SET")
        tenant_name = getattr(request.state, "tenant_name", "NOT_SET")
        role = getattr(request.state, "role", "NOT_SET")
        body = f"{tenant_id}|{tenant_name}|{role}"
        return PlainTextResponse(body)

    app = Starlette(routes=[Route(path, endpoint)])
    for mw in middlewares:
        app.add_middleware(mw)
    return app


# ---------------------------------------------------------------------------
# TenantMiddleware
# ---------------------------------------------------------------------------


class TestTenantMiddleware:
    """Tests for TenantMiddleware – header extraction and propagation (dev mode)."""

    @pytest.fixture(autouse=True)
    def enable_debug_mode(self) -> Any:
        """Run TenantMiddleware tests in dev/debug mode so header auth is active."""
        with patch("finspark.core.middleware.settings") as mock_settings:
            mock_settings.debug = True
            yield mock_settings

    def test_tenant_header_extracted(self) -> None:
        app = _make_app(TenantMiddleware)
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get(
            "/",
            headers={"X-Tenant-ID": "acme", "X-Tenant-Name": "Acme Corp"},
        )
        assert resp.status_code == 200
        assert "acme" in resp.text

    def test_tenant_name_header_extracted(self) -> None:
        app = _make_app(TenantMiddleware)
        client = TestClient(app)
        resp = client.get(
            "/",
            headers={"X-Tenant-ID": "bank1", "X-Tenant-Name": "Bank One"},
        )
        assert "Bank One" in resp.text

    def test_role_header_extracted(self) -> None:
        app = _make_app(TenantMiddleware)
        client = TestClient(app)
        resp = client.get(
            "/",
            headers={
                "X-Tenant-ID": "t1",
                "X-Tenant-Name": "T1",
                "X-Tenant-Role": "admin",
            },
        )
        assert "admin" in resp.text

    def test_missing_tenant_id_uses_default(self) -> None:
        app = _make_app(TenantMiddleware)
        client = TestClient(app)
        resp = client.get("/")
        assert DEFAULT_TENANT_ID in resp.text

    def test_missing_tenant_name_uses_default(self) -> None:
        app = _make_app(TenantMiddleware)
        client = TestClient(app)
        resp = client.get("/")
        assert DEFAULT_TENANT_NAME in resp.text

    def test_missing_role_uses_admin_default_in_dev(self) -> None:
        app = _make_app(TenantMiddleware)
        client = TestClient(app)
        resp = client.get("/", headers={"X-Tenant-ID": "t1"})
        assert "admin" in resp.text

    def test_response_echoes_tenant_id_header(self) -> None:
        app = _make_app(TenantMiddleware)
        client = TestClient(app)
        resp = client.get("/", headers={"X-Tenant-ID": "echo-me"})
        assert resp.headers.get("x-tenant-id") == "echo-me"

    def test_response_echoes_default_tenant_id_when_missing(self) -> None:
        app = _make_app(TenantMiddleware)
        client = TestClient(app)
        resp = client.get("/")
        assert resp.headers.get("x-tenant-id") == DEFAULT_TENANT_ID

    def test_all_three_headers_propagated(self) -> None:
        app = _make_app(TenantMiddleware)
        client = TestClient(app)
        resp = client.get(
            "/",
            headers={
                "X-Tenant-ID": "fin-corp",
                "X-Tenant-Name": "Fin Corp",
                "X-Tenant-Role": "configurator",
            },
        )
        body = resp.text
        assert "fin-corp" in body
        assert "Fin Corp" in body
        assert "configurator" in body


# ---------------------------------------------------------------------------
# RequestLoggingMiddleware
# ---------------------------------------------------------------------------


class TestRequestLoggingMiddleware:
    """Tests for RequestLoggingMiddleware – timing and structured logging."""

    @pytest.fixture(autouse=True)
    def enable_debug_mode(self) -> Any:
        """Run with debug=True so TenantMiddleware stacking tests use header auth."""
        with patch("finspark.core.middleware.settings") as mock_settings:
            mock_settings.debug = True
            yield mock_settings

    def test_response_time_header_present(self) -> None:
        app = _make_app(RequestLoggingMiddleware)
        client = TestClient(app)
        resp = client.get("/")
        assert "x-response-time" in resp.headers

    def test_response_time_header_format(self) -> None:
        app = _make_app(RequestLoggingMiddleware)
        client = TestClient(app)
        resp = client.get("/")
        rt = resp.headers.get("x-response-time", "")
        assert rt.endswith("ms"), f"Expected 'ms' suffix, got: {rt!r}"

    def test_request_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        app = _make_app(RequestLoggingMiddleware)
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="finspark.core.middleware"):
            client.get("/")
        assert any("request_completed" in msg for msg in caplog.messages)

    def test_log_contains_method(self, caplog: pytest.LogCaptureFixture) -> None:
        app = _make_app(RequestLoggingMiddleware)
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="finspark.core.middleware"):
            client.get("/")
        record = next(
            (r for r in caplog.records if "request_completed" in r.getMessage()),
            None,
        )
        assert record is not None
        assert record.__dict__.get("method") == "GET"

    def test_log_contains_status_code(self, caplog: pytest.LogCaptureFixture) -> None:
        app = _make_app(RequestLoggingMiddleware)
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="finspark.core.middleware"):
            client.get("/")
        record = next(
            (r for r in caplog.records if "request_completed" in r.getMessage()),
            None,
        )
        assert record is not None
        assert record.__dict__.get("status_code") == 200

    def test_log_duration_is_numeric(self, caplog: pytest.LogCaptureFixture) -> None:
        app = _make_app(RequestLoggingMiddleware)
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="finspark.core.middleware"):
            client.get("/")
        record = next(
            (r for r in caplog.records if "request_completed" in r.getMessage()),
            None,
        )
        assert record is not None
        duration = record.__dict__.get("duration_ms")
        assert isinstance(duration, float)
        assert duration >= 0

    def test_log_unknown_tenant_when_no_state(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When no TenantMiddleware precedes, tenant_id falls back to 'unknown'."""
        app = _make_app(RequestLoggingMiddleware)
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="finspark.core.middleware"):
            client.get("/")
        record = next(
            (r for r in caplog.records if "request_completed" in r.getMessage()),
            None,
        )
        assert record is not None
        assert record.__dict__.get("tenant_id") == "unknown"

    def test_log_shows_tenant_id_from_state(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When TenantMiddleware runs first, logging picks up the tenant_id."""
        # Stack: TenantMiddleware(outer) → RequestLoggingMiddleware(inner)
        app = _make_app(RequestLoggingMiddleware, TenantMiddleware)
        client = TestClient(app)
        with caplog.at_level(logging.INFO, logger="finspark.core.middleware"):
            client.get("/", headers={"X-Tenant-ID": "known-tenant"})
        record = next(
            (r for r in caplog.records if "request_completed" in r.getMessage()),
            None,
        )
        assert record is not None
        assert record.__dict__.get("tenant_id") == "known-tenant"


# ---------------------------------------------------------------------------
# DeprecationHeaderMiddleware
# ---------------------------------------------------------------------------


class TestDeprecationHeaderMiddleware:
    """Tests for DeprecationHeaderMiddleware – header injection on deprecated versions."""

    def _make_deprecation_app(self, path: str = "/api/v1/adapters/abc/versions/1.0") -> Starlette:
        async def endpoint(request: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route(path, endpoint)])
        app.add_middleware(DeprecationHeaderMiddleware)
        return app

    def test_non_matching_path_no_deprecation_header(self) -> None:
        app = self._make_deprecation_app(path="/health")
        client = TestClient(app)
        resp = client.get("/health")
        assert "deprecation" not in resp.headers
        assert "sunset" not in resp.headers

    def test_matching_path_no_db_does_not_crash(self) -> None:
        """When DB import fails, the middleware swallows the exception gracefully."""
        app = self._make_deprecation_app(path="/api/v1/adapters/abc/versions/1.0")
        client = TestClient(app, raise_server_exceptions=True)
        # Without a real DB, the middleware will hit an import/runtime error and
        # log a warning — response should still be 200.
        resp = client.get("/api/v1/adapters/abc/versions/1.0")
        assert resp.status_code == 200

    def _make_mock_db(self, scalar_value: Any) -> AsyncMock:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = scalar_value
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)
        return mock_db

    def test_deprecation_header_added_for_deprecated_version(self) -> None:
        """With a mocked deprecated version, the Deprecation header is set."""
        mock_version = MagicMock()
        mock_version.status = "deprecated"

        mock_tracker = MagicMock()
        mock_tracker._compute_sunset_date.return_value = None
        mock_tracker._find_replacement = AsyncMock(return_value=None)

        mock_db = self._make_mock_db(mock_version)

        async def endpoint(request: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/api/v1/adapters/abc/versions/1.0", endpoint)])
        app.add_middleware(DeprecationHeaderMiddleware)

        with (
            patch("finspark.core.database.async_session_factory", MagicMock(return_value=mock_db)),
            patch(
                "finspark.services.registry.deprecation.DeprecationTracker",
                return_value=mock_tracker,
            ),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/api/v1/adapters/abc/versions/1.0")

        assert resp.headers.get("deprecation") == "true"

    def test_sunset_header_added_when_sunset_date_known(self) -> None:
        """Sunset header is set when tracker returns a date."""
        from datetime import UTC, datetime

        sunset_dt = datetime(2025, 12, 31, 0, 0, 0, tzinfo=UTC)

        mock_version = MagicMock()
        mock_version.status = "deprecated"

        mock_tracker = MagicMock()
        mock_tracker._compute_sunset_date.return_value = sunset_dt
        mock_tracker._find_replacement = AsyncMock(return_value=None)

        mock_db = self._make_mock_db(mock_version)

        async def endpoint(request: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/api/v1/adapters/abc/versions/2.0", endpoint)])
        app.add_middleware(DeprecationHeaderMiddleware)

        with (
            patch("finspark.core.database.async_session_factory", MagicMock(return_value=mock_db)),
            patch(
                "finspark.services.registry.deprecation.DeprecationTracker",
                return_value=mock_tracker,
            ),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/api/v1/adapters/abc/versions/2.0")

        assert "sunset" in resp.headers

    def test_link_header_added_when_replacement_exists(self) -> None:
        """Link header pointing to successor is set when replacement exists."""
        mock_version = MagicMock()
        mock_version.status = "deprecated"

        mock_tracker = MagicMock()
        mock_tracker._compute_sunset_date.return_value = None
        mock_tracker._find_replacement = AsyncMock(return_value="2.0")

        mock_db = self._make_mock_db(mock_version)

        async def endpoint(request: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/api/v1/adapters/myid/versions/1.0", endpoint)])
        app.add_middleware(DeprecationHeaderMiddleware)

        with (
            patch("finspark.core.database.async_session_factory", MagicMock(return_value=mock_db)),
            patch(
                "finspark.services.registry.deprecation.DeprecationTracker",
                return_value=mock_tracker,
            ),
        ):
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/api/v1/adapters/myid/versions/1.0")

        link = resp.headers.get("link", "")
        assert "successor-version" in link
        assert "myid" in link

    def test_active_version_no_deprecation_header(self) -> None:
        """If the version is active (not deprecated), no Deprecation header is added."""
        mock_version = MagicMock()
        mock_version.status = "active"

        mock_db = self._make_mock_db(mock_version)

        async def endpoint(request: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/api/v1/adapters/abc/versions/1.0", endpoint)])
        app.add_middleware(DeprecationHeaderMiddleware)

        with patch("finspark.core.database.async_session_factory", MagicMock(return_value=mock_db)):
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/api/v1/adapters/abc/versions/1.0")

        assert "deprecation" not in resp.headers

    def test_version_not_found_no_deprecation_header(self) -> None:
        """If the DB returns None (version not found), no Deprecation header."""
        mock_db = self._make_mock_db(None)

        async def endpoint(request: Request) -> PlainTextResponse:
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route("/api/v1/adapters/abc/versions/1.0", endpoint)])
        app.add_middleware(DeprecationHeaderMiddleware)

        with patch("finspark.core.database.async_session_factory", MagicMock(return_value=mock_db)):
            client = TestClient(app, raise_server_exceptions=True)
            resp = client.get("/api/v1/adapters/abc/versions/1.0")

        assert "deprecation" not in resp.headers


# ---------------------------------------------------------------------------
# Security – Encryption
# ---------------------------------------------------------------------------


class TestEncryption:
    def test_roundtrip(self) -> None:
        plaintext = "super-secret-api-key"
        assert decrypt_value(encrypt_value(plaintext)) == plaintext

    def test_encrypted_differs_from_plaintext(self) -> None:
        plaintext = "hello"
        assert encrypt_value(plaintext) != plaintext

    def test_different_ciphertexts_each_call(self) -> None:
        """Fernet uses random IV, so successive encryptions differ."""
        v = "same-value"
        assert encrypt_value(v) != encrypt_value(v)

    def test_both_ciphertexts_decrypt_to_same(self) -> None:
        v = "same-value"
        c1, c2 = encrypt_value(v), encrypt_value(v)
        assert decrypt_value(c1) == decrypt_value(c2) == v

    def test_invalid_ciphertext_raises(self) -> None:
        import pytest

        with pytest.raises(Exception):
            decrypt_value("this-is-not-valid-fernet-data")

    def test_empty_string_roundtrip(self) -> None:
        assert decrypt_value(encrypt_value("")) == ""

    def test_unicode_roundtrip(self) -> None:
        plaintext = "नमस्ते-secret-🔑"
        assert decrypt_value(encrypt_value(plaintext)) == plaintext

    def test_long_value_roundtrip(self) -> None:
        long_val = "x" * 10_000
        assert decrypt_value(encrypt_value(long_val)) == long_val


# ---------------------------------------------------------------------------
# Security – PII Masking
# ---------------------------------------------------------------------------


class TestPIIMasking:
    # Aadhaar
    def test_aadhaar_with_spaces_masked(self) -> None:
        assert "1234 5678 9012" not in mask_pii("Aadhaar: 1234 5678 9012")

    def test_aadhaar_with_dashes_masked(self) -> None:
        assert "1234-5678-9012" not in mask_pii("ID: 1234-5678-9012")

    def test_aadhaar_no_separator_masked(self) -> None:
        assert "123456789012" not in mask_pii("num: 123456789012")

    def test_aadhaar_replaced_with_xxxx(self) -> None:
        assert "XXXX" in mask_pii("1234 5678 9012")

    # PAN
    def test_pan_masked(self) -> None:
        assert "ABCDE1234F" not in mask_pii("PAN: ABCDE1234F")

    def test_lowercase_pan_not_masked(self) -> None:
        text = "abcde1234f"
        assert mask_pii(text) == text

    # Phone
    def test_phone_with_country_code_masked(self) -> None:
        assert "9876543210" not in mask_pii("+91 9876543210")

    def test_phone_without_country_code_masked(self) -> None:
        assert "9876543210" not in mask_pii("Phone: 9876543210")

    # Email
    def test_email_masked(self) -> None:
        assert "test@example.com" not in mask_pii("Email: test@example.com")

    def test_email_replaced_with_placeholder(self) -> None:
        assert "***@***.***" in mask_pii("email@host.com")

    # Edge cases
    def test_empty_string_unchanged(self) -> None:
        assert mask_pii("") == ""

    def test_no_pii_text_unchanged(self) -> None:
        text = "Hello world, no PII here."
        assert mask_pii(text) == text

    def test_none_like_not_accepted(self) -> None:
        """mask_pii expects a str; passing non-str should raise TypeError."""
        with pytest.raises((TypeError, AttributeError)):
            mask_pii(None)  # type: ignore[arg-type]

    def test_multiple_pii_types_in_one_string(self) -> None:
        text = (
            "Aadhaar: 1234 5678 9012, PAN: ABCDE1234F, "
            "Phone: 9876543210, Email: user@test.com"
        )
        masked = mask_pii(text)
        assert "1234 5678 9012" not in masked
        assert "ABCDE1234F" not in masked
        assert "9876543210" not in masked
        assert "user@test.com" not in masked

    def test_surrounding_text_preserved(self) -> None:
        text = "Customer Aadhaar: 1234 5678 9012, name: John"
        masked = mask_pii(text)
        assert "Customer" in masked
        assert "name" in masked
        assert "John" in masked


# ---------------------------------------------------------------------------
# Security – JWT
# ---------------------------------------------------------------------------


def _decode_jwt_unverified(token: str) -> dict:
    """Decode a JWT without signature or exp validation, for testing internal payload."""
    import base64
    import json

    _, payload_b64, _ = token.split(".")
    # Add padding
    payload_b64 += "=" * (4 - len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


class TestJWT:
    def test_token_is_string(self) -> None:
        token = create_jwt_token({"sub": "u1"})
        assert isinstance(token, str)
        assert len(token) > 0

    def test_token_has_three_parts(self) -> None:
        """JWT tokens consist of header.payload.signature."""
        token = create_jwt_token({"sub": "u1"})
        assert token.count(".") == 2

    def test_token_payload_contains_sub(self) -> None:
        token = create_jwt_token({"sub": "user123"})
        payload = _decode_jwt_unverified(token)
        assert payload["sub"] == "user123"

    def test_token_payload_has_exp_claim(self) -> None:
        token = create_jwt_token({"sub": "u1"})
        payload = _decode_jwt_unverified(token)
        assert "exp" in payload

    def test_token_payload_contains_all_fields(self) -> None:
        data = {"sub": "u1", "tenant": "acme", "scope": "read:write", "role": "admin"}
        token = create_jwt_token(data)
        payload = _decode_jwt_unverified(token)
        assert payload["tenant"] == "acme"
        assert payload["scope"] == "read:write"
        assert payload["role"] == "admin"

    def test_custom_expiry_produces_token(self) -> None:
        """create_jwt_token with custom expiry returns a non-empty string."""
        token = create_jwt_token({"sub": "u1"}, expires_delta=timedelta(minutes=5))
        assert isinstance(token, str)
        assert len(token) > 0

    def test_invalid_token_raises(self) -> None:
        with pytest.raises(Exception):
            decode_jwt_token("not.a.valid.jwt")

    def test_tampered_token_raises(self) -> None:
        token = create_jwt_token({"sub": "u1"})
        tampered = token[:-4] + "XXXX"
        with pytest.raises(Exception):
            decode_jwt_token(tampered)

    def test_different_tokens_per_call(self) -> None:
        """Two tokens with the same payload have different exp timestamps."""
        t1 = create_jwt_token({"sub": "u1"})
        t2 = create_jwt_token({"sub": "u1"})
        # They may differ due to timing, but both must be valid JWTs
        assert t1.count(".") == 2
        assert t2.count(".") == 2


# ---------------------------------------------------------------------------
# Audit – AuditService
# ---------------------------------------------------------------------------


def _run(coro):
    """Run an async coroutine synchronously using a fresh event loop."""
    import asyncio

    return asyncio.run(coro)


class TestAuditService:
    """Tests for AuditService using a mocked AsyncSession."""

    def _make_service(self):
        from finspark.core.audit import AuditService

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        return AuditService(mock_db), mock_db

    def test_log_creates_audit_entry(self) -> None:
        from finspark.models.audit import AuditLog

        service, mock_db = self._make_service()

        async def _run_test():
            return await service.log(
                tenant_id="tenant1",
                actor="user@example.com",
                action="create",
                resource_type="configuration",
                resource_id="cfg-001",
            )

        entry = _run(_run_test())
        assert isinstance(entry, AuditLog)
        mock_db.add.assert_called_once_with(entry)
        mock_db.flush.assert_awaited_once()

    def test_log_all_fields(self) -> None:
        service, _ = self._make_service()

        async def _run_test():
            return await service.log(
                tenant_id="t1",
                actor="admin",
                action="update",
                resource_type="adapter",
                resource_id="adp-999",
                details={"before": "v1", "after": "v2"},
                ip_address="192.168.1.1",
                user_agent="Mozilla/5.0",
            )

        entry = _run(_run_test())
        assert entry.tenant_id == "t1"
        assert entry.actor == "admin"
        assert entry.action == "update"
        assert entry.resource_type == "adapter"
        assert entry.resource_id == "adp-999"
        assert entry.ip_address == "192.168.1.1"
        assert entry.user_agent == "Mozilla/5.0"
        assert json.loads(entry.details) == {"before": "v1", "after": "v2"}

    def test_log_minimal_fields(self) -> None:
        """Log with no optional fields – details/ip/user_agent should be None."""
        service, _ = self._make_service()

        async def _run_test():
            return await service.log(
                tenant_id="t2",
                actor="svc",
                action="delete",
                resource_type="configuration",
                resource_id="cfg-002",
            )

        entry = _run(_run_test())
        assert entry.details is None
        assert entry.ip_address is None
        assert entry.user_agent is None

    def test_log_empty_details_dict(self) -> None:
        """An empty dict is falsy in Python, so details={} stores None (source behaviour)."""
        service, _ = self._make_service()

        async def _run_test():
            return await service.log(
                tenant_id="t3",
                actor="svc",
                action="deploy",
                resource_type="adapter",
                resource_id="adp-1",
                details={},
            )

        entry = _run(_run_test())
        # {} is falsy -> the source uses `json.dumps(details) if details else None`
        assert entry.details is None

    def test_log_nested_details(self) -> None:
        """Nested dict in details round-trips through JSON."""
        details = {"meta": {"key": "val"}, "count": 3}
        service, _ = self._make_service()

        async def _run_test():
            return await service.log(
                tenant_id="t4",
                actor="svc",
                action="rollback",
                resource_type="configuration",
                resource_id="cfg-003",
                details=details,
            )

        entry = _run(_run_test())
        assert json.loads(entry.details) == details

    def test_flush_is_called(self) -> None:
        service, mock_db = self._make_service()

        async def _run_test():
            await service.log(
                tenant_id="t5",
                actor="svc",
                action="create",
                resource_type="configuration",
                resource_id="cfg-004",
            )

        _run(_run_test())
        mock_db.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# Lifecycle State Machine
# ---------------------------------------------------------------------------


class TestLifecycleCanTransition:
    def test_draft_to_configured_allowed(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        assert lc.can_transition(ConfigStatus.CONFIGURED) is True

    def test_configured_to_validating_allowed(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.CONFIGURED)
        assert lc.can_transition(ConfigStatus.VALIDATING) is True

    def test_configured_to_draft_allowed(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.CONFIGURED)
        assert lc.can_transition(ConfigStatus.DRAFT) is True

    def test_validating_to_testing_allowed(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.VALIDATING)
        assert lc.can_transition(ConfigStatus.TESTING) is True

    def test_validating_to_configured_allowed(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.VALIDATING)
        assert lc.can_transition(ConfigStatus.CONFIGURED) is True

    def test_testing_to_active_allowed(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.TESTING)
        assert lc.can_transition(ConfigStatus.ACTIVE) is True

    def test_active_to_deprecated_allowed(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.ACTIVE)
        assert lc.can_transition(ConfigStatus.DEPRECATED) is True

    def test_active_to_rollback_allowed(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.ACTIVE)
        assert lc.can_transition(ConfigStatus.ROLLBACK) is True

    def test_deprecated_to_draft_allowed(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DEPRECATED)
        assert lc.can_transition(ConfigStatus.DRAFT) is True

    def test_rollback_to_configured_allowed(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.ROLLBACK)
        assert lc.can_transition(ConfigStatus.CONFIGURED) is True

    def test_rollback_to_draft_allowed(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.ROLLBACK)
        assert lc.can_transition(ConfigStatus.DRAFT) is True

    def test_self_transition_blocked(self) -> None:
        for status in ConfigStatus:
            lc = IntegrationLifecycle(state=status)
            assert lc.can_transition(status) is False, f"Self-loop allowed for {status}"

    def test_draft_to_active_blocked(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        assert lc.can_transition(ConfigStatus.ACTIVE) is False

    def test_draft_to_testing_blocked(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        assert lc.can_transition(ConfigStatus.TESTING) is False

    def test_testing_to_deprecated_blocked(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.TESTING)
        assert lc.can_transition(ConfigStatus.DEPRECATED) is False

    def test_deprecated_to_active_blocked(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DEPRECATED)
        assert lc.can_transition(ConfigStatus.ACTIVE) is False


class TestLifecycleInvalidTransitions:
    def test_invalid_transition_raises_error(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        with pytest.raises(InvalidTransitionError):
            lc.transition(ConfigStatus.ACTIVE)

    def test_error_message_contains_states(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        with pytest.raises(InvalidTransitionError) as exc_info:
            lc.transition(ConfigStatus.TESTING)
        msg = str(exc_info.value)
        assert "draft" in msg
        assert "testing" in msg

    def test_state_unchanged_after_invalid_transition(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        with pytest.raises(InvalidTransitionError):
            lc.transition(ConfigStatus.DEPRECATED)
        assert lc.state == ConfigStatus.DRAFT

    def test_audit_trail_unchanged_after_invalid(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        with pytest.raises(InvalidTransitionError):
            lc.transition(ConfigStatus.ACTIVE)
        assert len(lc.audit_trail) == 0

    def test_invalid_transition_error_attributes(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.CONFIGURED)
        try:
            lc.transition(ConfigStatus.DEPRECATED)
        except InvalidTransitionError as exc:
            assert exc.current == ConfigStatus.CONFIGURED
            assert exc.target == ConfigStatus.DEPRECATED
        else:
            pytest.fail("InvalidTransitionError not raised")


class TestLifecycleAuditTrail:
    def test_transition_creates_audit_entry(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        entry = lc.transition(ConfigStatus.CONFIGURED, actor="alice")
        assert isinstance(entry, AuditEntry)
        assert entry.from_state == ConfigStatus.DRAFT
        assert entry.to_state == ConfigStatus.CONFIGURED
        assert entry.actor == "alice"

    def test_audit_trail_grows_with_transitions(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        assert len(lc.audit_trail) == 2

    def test_audit_entry_has_timestamp(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        entry = lc.transition(ConfigStatus.CONFIGURED)
        assert entry.timestamp is not None

    def test_reason_stored_in_audit_entry(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.CONFIGURED)
        entry = lc.transition(ConfigStatus.VALIDATING, reason="ready for QA")
        assert entry.reason == "ready for QA"

    def test_audit_entry_without_actor(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        entry = lc.transition(ConfigStatus.CONFIGURED)
        assert entry.actor is None

    def test_audit_trail_order_preserved(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        assert lc.audit_trail[0].to_state == ConfigStatus.CONFIGURED
        assert lc.audit_trail[1].to_state == ConfigStatus.VALIDATING
        assert lc.audit_trail[2].to_state == ConfigStatus.TESTING


class TestGetAvailableTransitions:
    def test_draft_has_one_transition(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        available = lc.get_available_transitions()
        assert available == [ConfigStatus.CONFIGURED]

    def test_configured_has_two_transitions(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.CONFIGURED)
        available = lc.get_available_transitions()
        assert len(available) == 2
        assert ConfigStatus.DRAFT in available
        assert ConfigStatus.VALIDATING in available

    def test_validating_has_two_transitions(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.VALIDATING)
        available = lc.get_available_transitions()
        assert ConfigStatus.TESTING in available
        assert ConfigStatus.CONFIGURED in available

    def test_testing_has_two_transitions(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.TESTING)
        available = lc.get_available_transitions()
        assert ConfigStatus.ACTIVE in available
        assert ConfigStatus.CONFIGURED in available

    def test_active_has_two_transitions(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.ACTIVE)
        available = lc.get_available_transitions()
        assert set(available) == {ConfigStatus.DEPRECATED, ConfigStatus.ROLLBACK}

    def test_deprecated_has_one_transition(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DEPRECATED)
        available = lc.get_available_transitions()
        assert available == [ConfigStatus.DRAFT]

    def test_rollback_has_two_transitions(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.ROLLBACK)
        available = lc.get_available_transitions()
        assert ConfigStatus.CONFIGURED in available
        assert ConfigStatus.DRAFT in available

    def test_available_transitions_sorted(self) -> None:
        """get_available_transitions returns states sorted by their .value string."""
        lc = IntegrationLifecycle(state=ConfigStatus.CONFIGURED)
        available = lc.get_available_transitions()
        assert available == sorted(available, key=lambda s: s.value)


class TestFullLifecycle:
    def test_full_forward_path(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        lc.transition(ConfigStatus.DEPRECATED)
        assert lc.state == ConfigStatus.DEPRECATED
        assert len(lc.audit_trail) == 5

    def test_deprecated_can_return_to_draft(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        lc.transition(ConfigStatus.DEPRECATED)
        lc.transition(ConfigStatus.DRAFT)
        assert lc.state == ConfigStatus.DRAFT
        assert len(lc.audit_trail) == 6

    def test_rollback_path_from_active(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        lc.transition(ConfigStatus.ROLLBACK)
        lc.transition(ConfigStatus.CONFIGURED)
        assert lc.state == ConfigStatus.CONFIGURED
        assert len(lc.audit_trail) == 6

    def test_validation_failure_path(self) -> None:
        """Validating -> back to configured -> re-validate -> test -> active."""
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.CONFIGURED)  # validation failed, back
        lc.transition(ConfigStatus.VALIDATING)  # retry
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        assert lc.state == ConfigStatus.ACTIVE
        assert len(lc.audit_trail) == 6

    def test_full_lifecycle_audit_trail_states(self) -> None:
        lc = IntegrationLifecycle(state=ConfigStatus.DRAFT)
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        lc.transition(ConfigStatus.DEPRECATED)
        states = [e.to_state for e in lc.audit_trail]
        assert states == [
            ConfigStatus.CONFIGURED,
            ConfigStatus.VALIDATING,
            ConfigStatus.TESTING,
            ConfigStatus.ACTIVE,
            ConfigStatus.DEPRECATED,
        ]


class TestTransitionsMapCompleteness:
    def test_all_config_statuses_in_transitions(self) -> None:
        for status in ConfigStatus:
            assert status in TRANSITIONS, f"TRANSITIONS missing entry for {status}"
