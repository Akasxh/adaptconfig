"""Tests for the proxy router service."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.configuration import Configuration
from finspark.services.proxy.router import ProxyRouter, _circuit_breaker


def _make_config(
    config_id: str = "cfg-1",
    tenant_id: str = "test-tenant",
    base_url: str = "https://api.example.com",
    auth_type: str = "api_key",
    field_mappings: list | None = None,
) -> Configuration:
    mappings = field_mappings or [
        {"source_field": "name", "target_field": "full_name"},
    ]
    full = {
        "base_url": base_url,
        "adapter_name": "test-adapter",
        "version": "v1",
        "auth": {
            "type": auth_type,
            "credentials": {"api_key": "test-key"},
        },
        "retry_count": 2,
        "retry_backoff": 0.01,
    }
    config = Configuration(
        id=config_id,
        tenant_id=tenant_id,
        name="Test Config",
        adapter_version_id="av-1",
        status="active",
        version=1,
        field_mappings=json.dumps(mappings),
        full_config=json.dumps(full),
    )
    return config


def _mock_httpx_response(status: int = 200, body: dict | None = None) -> httpx.Response:
    resp = httpx.Response(
        status_code=status,
        json=body or {"result": "ok"},
        request=httpx.Request("POST", "https://api.example.com/endpoint"),
    )
    return resp


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    _circuit_breaker._failures.clear()
    _circuit_breaker._opened_at.clear()
    yield


class TestProxyBasicPost:
    @pytest.mark.asyncio
    async def test_proxy_basic_post(self, db_session: AsyncSession) -> None:
        config = _make_config()
        db_session.add(config)
        await db_session.flush()

        mock_response = _mock_httpx_response(200, {"result": "ok"})

        with patch("finspark.services.proxy.router.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            proxy = ProxyRouter(db_session)
            result = await proxy.proxy_request(
                config_id=config.id,
                endpoint_path="endpoint",
                tenant_id="test-tenant",
                request_body={"name": "Rajesh"},
                request_method="POST",
            )

        assert result.success is True
        assert result.status_code == 200
        assert result.response_body == {"result": "ok"}


class TestProxyAuthInjection:
    @pytest.mark.asyncio
    async def test_proxy_auth_injection_bearer(self, db_session: AsyncSession) -> None:
        full = {
            "base_url": "https://api.example.com",
            "adapter_name": "test",
            "version": "v1",
            "auth": {"type": "bearer", "credentials": {"token": "my-bearer"}},
            "retry_count": 0,
        }
        config = Configuration(
            id="cfg-bearer",
            tenant_id="test-tenant",
            name="Bearer Config",
            adapter_version_id="av-1",
            status="active",
            version=1,
            field_mappings="[]",
            full_config=json.dumps(full),
        )
        db_session.add(config)
        await db_session.flush()

        mock_response = _mock_httpx_response(200)
        captured_kwargs: dict = {}

        async def capture_request(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_response

        with patch("finspark.services.proxy.router.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=capture_request)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            proxy = ProxyRouter(db_session)
            await proxy.proxy_request(
                config_id="cfg-bearer",
                endpoint_path="ep",
                tenant_id="test-tenant",
                request_body={"data": 1},
            )

        assert captured_kwargs["headers"]["Authorization"] == "Bearer my-bearer"

    @pytest.mark.asyncio
    async def test_proxy_auth_injection_api_key(self, db_session: AsyncSession) -> None:
        full = {
            "base_url": "https://api.example.com",
            "adapter_name": "test",
            "version": "v1",
            "auth": {"type": "api_key", "credentials": {"api_key": "sk-123"}},
            "retry_count": 0,
        }
        config = Configuration(
            id="cfg-apikey",
            tenant_id="test-tenant",
            name="API Key Config",
            adapter_version_id="av-1",
            status="active",
            version=1,
            field_mappings="[]",
            full_config=json.dumps(full),
        )
        db_session.add(config)
        await db_session.flush()

        captured_kwargs: dict = {}

        async def capture_request(**kwargs):
            captured_kwargs.update(kwargs)
            return _mock_httpx_response(200)

        with patch("finspark.services.proxy.router.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=capture_request)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            proxy = ProxyRouter(db_session)
            await proxy.proxy_request(
                config_id="cfg-apikey",
                endpoint_path="ep",
                tenant_id="test-tenant",
                request_body={},
            )

        assert captured_kwargs["headers"]["X-API-Key"] == "sk-123"


class TestProxyRetry:
    @pytest.mark.asyncio
    async def test_proxy_retry_on_5xx(self, db_session: AsyncSession) -> None:
        config = _make_config()
        db_session.add(config)
        await db_session.flush()

        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_httpx_response(500, {"error": "server error"})
            return _mock_httpx_response(200, {"result": "ok"})

        with patch("finspark.services.proxy.router.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=side_effect)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            proxy = ProxyRouter(db_session)
            result = await proxy.proxy_request(
                config_id=config.id,
                endpoint_path="ep",
                tenant_id="test-tenant",
                request_body={"name": "test"},
            )

        assert result.success is True
        assert result.status_code == 200
        assert result.retries_attempted == 1
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_proxy_no_retry_on_4xx(self, db_session: AsyncSession) -> None:
        config = _make_config()
        db_session.add(config)
        await db_session.flush()

        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            return _mock_httpx_response(400, {"error": "bad request"})

        with patch("finspark.services.proxy.router.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=side_effect)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            proxy = ProxyRouter(db_session)
            result = await proxy.proxy_request(
                config_id=config.id,
                endpoint_path="ep",
                tenant_id="test-tenant",
                request_body={"name": "test"},
            )

        assert result.success is False
        assert result.status_code == 400
        assert call_count == 1
        assert result.retries_attempted == 0


class TestProxyLogging:
    @pytest.mark.asyncio
    async def test_proxy_logs_call(self, db_session: AsyncSession) -> None:
        config = _make_config()
        db_session.add(config)
        await db_session.flush()

        mock_response = _mock_httpx_response(200)

        with patch("finspark.services.proxy.router.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            proxy = ProxyRouter(db_session)
            with patch.object(proxy.logger, "log_call", new_callable=AsyncMock) as mock_log:
                await proxy.proxy_request(
                    config_id=config.id,
                    endpoint_path="ep",
                    tenant_id="test-tenant",
                    request_body={"name": "test"},
                )
                mock_log.assert_called_once()
                call_kwargs = mock_log.call_args[1]
                assert call_kwargs["tenant_id"] == "test-tenant"
                assert call_kwargs["configuration_id"] == config.id
                assert call_kwargs["response_status"] == 200


class TestProxyErrors:
    @pytest.mark.asyncio
    async def test_proxy_config_not_found(self, db_session: AsyncSession) -> None:
        proxy = ProxyRouter(db_session)
        result = await proxy.proxy_request(
            config_id="nonexistent",
            endpoint_path="ep",
            tenant_id="test-tenant",
        )
        assert result.success is False
        assert result.status_code == 404
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_proxy_wrong_tenant(self, db_session: AsyncSession) -> None:
        config = _make_config(tenant_id="tenant-a")
        db_session.add(config)
        await db_session.flush()

        proxy = ProxyRouter(db_session)
        result = await proxy.proxy_request(
            config_id=config.id,
            endpoint_path="ep",
            tenant_id="tenant-b",
        )
        assert result.success is False
        assert result.status_code == 404


class TestProxyTransform:
    @pytest.mark.asyncio
    async def test_proxy_transform_applied(self, db_session: AsyncSession) -> None:
        config = _make_config(
            field_mappings=[
                {"source_field": "name", "target_field": "full_name", "transformation": "upper"},
            ],
        )
        db_session.add(config)
        await db_session.flush()

        captured_kwargs: dict = {}

        async def capture_request(**kwargs):
            captured_kwargs.update(kwargs)
            return _mock_httpx_response(200)

        with patch("finspark.services.proxy.router.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.request = AsyncMock(side_effect=capture_request)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            proxy = ProxyRouter(db_session)
            await proxy.proxy_request(
                config_id=config.id,
                endpoint_path="ep",
                tenant_id="test-tenant",
                request_body={"name": "rajesh"},
            )

        assert captured_kwargs["json"] == {"full_name": "RAJESH"}
