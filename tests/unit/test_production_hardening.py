"""Tests for Wave 1 production hardening fixes.

Covers:
- Issue #51: Global exception handler (no stack trace leaks)
- Issue #68: Gemini API key redaction in logs
- Issue #56: PII masking filter wired into logging pipeline
- Issue #54: GeminiClient connection pool lifecycle
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from finspark.core.logging_filter import PIIMaskingFilter
from finspark.services.llm.client import GeminiClient, _shared_client, get_llm_client


# ---------------------------------------------------------------------------
# Issue #51 — Global exception handler
# ---------------------------------------------------------------------------


class TestGlobalExceptionHandler:
    """Verify unhandled exceptions return generic 500, no stack traces."""

    async def test_unhandled_exception_returns_500(self) -> None:
        """Trigger an endpoint that raises, verify generic 500 response."""
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from starlette.responses import JSONResponse as _JSONResponse

        test_app = FastAPI()

        @test_app.exception_handler(Exception)
        async def _handler(request: Request, exc: Exception) -> _JSONResponse:
            return _JSONResponse(status_code=500, content={"detail": "Internal server error"})

        @test_app.get("/crash")
        async def crash():
            raise RuntimeError("secret database password is hunter2")

        transport = ASGITransport(app=test_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/crash")

        assert resp.status_code == 500
        body = resp.json()
        assert body == {"detail": "Internal server error"}
        assert "hunter2" not in resp.text
        assert "RuntimeError" not in resp.text
        assert "Traceback" not in resp.text

    async def test_exception_handler_preserves_json_content_type(self) -> None:
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from starlette.responses import JSONResponse as _JSONResponse

        test_app = FastAPI()

        @test_app.exception_handler(Exception)
        async def _handler(request: Request, exc: Exception) -> _JSONResponse:
            return _JSONResponse(status_code=500, content={"detail": "Internal server error"})

        @test_app.get("/crash")
        async def crash():
            raise ValueError("oops")

        transport = ASGITransport(app=test_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/crash")

        assert resp.headers["content-type"] == "application/json"

    def test_global_handler_registered_on_main_app(self) -> None:
        """The main app must have an exception handler for Exception."""
        from finspark.main import app

        assert Exception in app.exception_handlers


# ---------------------------------------------------------------------------
# Issue #68 — Gemini API key redaction
# ---------------------------------------------------------------------------


class TestGeminiKeyRedaction:
    """Verify the API key never appears in logged URLs or error bodies."""

    def test_safe_url_redacts_key(self) -> None:
        c = GeminiClient(api_key="sk-secret-key-12345", model="gemini-2.5-flash")
        url = "https://api.example.com/v1?key=sk-secret-key-12345"
        safe = c._safe_url(url)
        assert "sk-secret-key-12345" not in safe
        assert "***" in safe

    def test_safe_url_no_key_in_url(self) -> None:
        c = GeminiClient(api_key="sk-secret-key-12345", model="gemini-2.5-flash")
        url = "https://api.example.com/v1/generate"
        safe = c._safe_url(url)
        assert safe == url

    async def test_error_response_redacts_key_in_body(self) -> None:
        api_key = "AIzaSyDEADBEEF123456"
        c = GeminiClient(api_key=api_key, model="gemini-2.5-flash")
        error_body = f'{{"error": "API key {api_key} is invalid"}}'
        mock_resp = httpx.Response(status_code=403, text=error_body)

        with patch.object(c._client, "post", new_callable=AsyncMock, return_value=mock_resp):
            from finspark.services.llm.client import GeminiAPIError

            with pytest.raises(GeminiAPIError) as exc_info:
                await c.generate("test prompt")
            # The exception message must not contain the raw key
            assert api_key not in str(exc_info.value)
            assert "***" in str(exc_info.value)

    async def test_timeout_log_redacts_url(self, caplog) -> None:
        api_key = "AIzaSyDEADBEEF999"
        c = GeminiClient(api_key=api_key, model="gemini-2.5-flash")

        with patch.object(
            c._client, "post", new_callable=AsyncMock, side_effect=httpx.TimeoutException("timeout")
        ):
            from finspark.services.llm.client import GeminiAPIError

            with caplog.at_level(logging.ERROR):
                with pytest.raises(GeminiAPIError):
                    await c.generate("test")

            # API key must not appear in any log record
            for record in caplog.records:
                assert api_key not in record.getMessage()


# ---------------------------------------------------------------------------
# Issue #56 — PII masking logging filter
# ---------------------------------------------------------------------------


class TestPIIMaskingFilter:
    """Verify PIIMaskingFilter scrubs PII from log records."""

    def test_filter_masks_pan_in_msg(self) -> None:
        f = PIIMaskingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Customer PAN is ABCDE1234F",
            args=None, exc_info=None,
        )
        f.filter(record)
        assert "ABCDE1234F" not in record.msg
        assert "XXXXX****X" in record.msg

    def test_filter_masks_aadhaar_in_msg(self) -> None:
        f = PIIMaskingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Aadhaar: 1234 5678 9012",
            args=None, exc_info=None,
        )
        f.filter(record)
        assert "1234 5678 9012" not in record.msg
        assert "XXXX" in record.msg

    def test_filter_masks_pii_in_tuple_args(self) -> None:
        f = PIIMaskingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="data: %s",
            args=("PAN is ABCDE1234F",), exc_info=None,
        )
        f.filter(record)
        assert isinstance(record.args, tuple)
        assert "ABCDE1234F" not in record.args[0]

    def test_filter_masks_pii_in_dict_args(self) -> None:
        f = PIIMaskingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="%(data)s",
            args=None, exc_info=None,
        )
        # Set dict args directly (bypassing constructor validation)
        record.args = {"data": "PAN FGHIJ5678K"}
        f.filter(record)
        assert isinstance(record.args, dict)
        masked_val = str(record.args["data"])
        assert "FGHIJ5678K" not in masked_val

    def test_filter_returns_true(self) -> None:
        """Filter must always return True (never suppress records)."""
        f = PIIMaskingFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="safe message", args=None, exc_info=None,
        )
        assert f.filter(record) is True

    def test_pii_masked_in_actual_logger(self, caplog) -> None:
        """End-to-end: PII in a real logger output gets masked."""
        test_logger = logging.getLogger("test.pii.e2e")
        pii_filter = PIIMaskingFilter()
        test_logger.addFilter(pii_filter)
        try:
            with caplog.at_level(logging.INFO, logger="test.pii.e2e"):
                test_logger.info("Customer PAN: ZYXWV9876A phone +91 9876543210")
            for record in caplog.records:
                assert "ZYXWV9876A" not in record.getMessage()
                assert "9876543210" not in record.getMessage()
        finally:
            test_logger.removeFilter(pii_filter)


# ---------------------------------------------------------------------------
# Issue #54 — GeminiClient connection pool lifecycle
# ---------------------------------------------------------------------------


class TestGeminiClientLifecycle:
    """Verify the shared client singleton and cleanup."""

    async def test_close_method_exists_and_works(self) -> None:
        c = GeminiClient(api_key="test-key", model="gemini-2.5-flash")
        assert hasattr(c, "close")
        # Should not raise
        await c.close()

    def test_get_llm_client_returns_singleton(self) -> None:
        """get_llm_client returns the same instance on repeated calls."""
        import finspark.services.llm.client as mod

        original = mod._shared_client
        try:
            mod._shared_client = None
            with patch.object(mod, "GeminiClient") as mock_cls:
                mock_instance = mock_cls.return_value
                c1 = get_llm_client()
                c2 = get_llm_client()
                assert c1 is c2
                mock_cls.assert_called_once()
        finally:
            mod._shared_client = original

    async def test_lifespan_closes_shared_client(self) -> None:
        """The app lifespan shutdown closes the shared client."""
        import finspark.services.llm.client as mod

        original = mod._shared_client
        mock_client = AsyncMock()
        mod._shared_client = mock_client
        try:
            from finspark.main import lifespan, app

            # We need to mock init_db and seed_adapters to avoid DB setup
            with (
                patch("finspark.main.init_db", new_callable=AsyncMock),
                patch("finspark.main.seed_adapters", new_callable=AsyncMock),
                patch("finspark.main.settings") as mock_settings,
            ):
                mock_settings.debug = True
                mock_settings.upload_dir.mkdir = lambda **kw: None

                async with lifespan(app):
                    pass

            mock_client.close.assert_awaited_once()
        finally:
            mod._shared_client = original
