"""Tests for CallLogger service: logging, PII masking, filtering, comparison."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.services.observability.call_logger import CallLogger, _mask_pii

TENANT = "test-tenant"


async def _log_sample(
    logger: CallLogger,
    *,
    tenant_id: str = TENANT,
    adapter_name: str = "razorpay",
    adapter_version: str = "2.0",
    endpoint_path: str = "/v1/payments",
    http_method: str = "POST",
    response_status: int = 200,
    response_time_ms: int = 120,
    request_body: dict | None = None,
    request_headers: dict | None = None,
    response_body: dict | None = None,
    schema_match: bool = True,
    error_code: str | None = None,
):
    return await logger.log_call(
        tenant_id=tenant_id,
        configuration_id="cfg-001",
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        endpoint_path=endpoint_path,
        http_method=http_method,
        request_headers=request_headers,
        request_body=request_body,
        response_status=response_status,
        response_headers=None,
        response_body=response_body,
        response_time_ms=response_time_ms,
        schema_match=schema_match,
        error_code=error_code,
    )


class TestLogCallBasic:
    @pytest.mark.asyncio
    async def test_log_call_basic(self, db_session: AsyncSession) -> None:
        cl = CallLogger(db_session)
        log = await _log_sample(cl)

        assert log.id is not None
        assert log.tenant_id == TENANT
        assert log.adapter_name == "razorpay"
        assert log.response_status == 200
        assert log.response_time_ms == 120
        assert log.schema_match is True


class TestPIIMasking:
    @pytest.mark.asyncio
    async def test_log_call_pii_masking(self, db_session: AsyncSession) -> None:
        cl = CallLogger(db_session)
        body = {
            "aadhaar": "123456789012",
            "pan": "ABCDE1234F",
            "phone": "9876543210",
            "email": "user@example.com",
            "name": "Test User",
        }
        log = await _log_sample(cl, request_body=body)
        stored = json.loads(log.request_body)

        assert stored["aadhaar"] == "XXXX-XXXX-XXXX"
        assert stored["pan"] == "XXXXX****X"
        assert stored["phone"] == "XXXXXX****"
        assert stored["email"] == "***@***.***"
        assert stored["name"] == "Test User"

    @pytest.mark.asyncio
    async def test_log_call_auth_header_redacted(self, db_session: AsyncSession) -> None:
        cl = CallLogger(db_session)
        headers = {
            "Authorization": "Bearer secret-token-123",
            "Content-Type": "application/json",
            "x-api-key": "sk_live_abc123",
        }
        log = await _log_sample(cl, request_headers=headers)
        stored = json.loads(log.request_headers)

        assert stored["Authorization"] == "[REDACTED]"
        assert stored["Content-Type"] == "application/json"
        assert stored["x-api-key"] == "[REDACTED]"

    def test_mask_pii_nested(self) -> None:
        data = {
            "user": {
                "email": "deep@nested.com",
                "password": "secret123",
            },
            "items": [{"aadhaar": "111122223333"}],
        }
        masked = _mask_pii(data)
        assert masked["user"]["email"] == "***@***.***"
        assert masked["user"]["password"] == "[REDACTED]"
        assert masked["items"][0]["aadhaar"] == "XXXX-XXXX-XXXX"


class TestGetCallsFilters:
    @pytest.mark.asyncio
    async def test_get_calls_filter_by_adapter(self, db_session: AsyncSession) -> None:
        cl = CallLogger(db_session)
        await _log_sample(cl, adapter_name="razorpay")
        await _log_sample(cl, adapter_name="paytm")
        await _log_sample(cl, adapter_name="razorpay")

        results = await cl.get_calls(TENANT, adapter_name="razorpay")
        assert len(results) == 2
        assert all(r.adapter_name == "razorpay" for r in results)

    @pytest.mark.asyncio
    async def test_get_calls_filter_by_version(self, db_session: AsyncSession) -> None:
        cl = CallLogger(db_session)
        await _log_sample(cl, adapter_version="1.0")
        await _log_sample(cl, adapter_version="2.0")
        await _log_sample(cl, adapter_version="2.0")

        results = await cl.get_calls(TENANT, adapter_version="2.0")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_get_calls_filter_by_status_range(self, db_session: AsyncSession) -> None:
        cl = CallLogger(db_session)
        await _log_sample(cl, response_status=200)
        await _log_sample(cl, response_status=400)
        await _log_sample(cl, response_status=500)

        results = await cl.get_calls(TENANT, status_min=400, status_max=500)
        assert len(results) == 2
        assert all(400 <= r.response_status <= 500 for r in results)

    @pytest.mark.asyncio
    async def test_get_calls_filter_by_date_range(self, db_session: AsyncSession) -> None:
        cl = CallLogger(db_session)
        await _log_sample(cl)
        await _log_sample(cl)

        now = datetime.now(timezone.utc)
        results = await cl.get_calls(
            TENANT,
            from_date=now - timedelta(minutes=5),
            to_date=now + timedelta(minutes=5),
        )
        assert len(results) == 2

        results = await cl.get_calls(
            TENANT,
            from_date=now + timedelta(hours=1),
        )
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_get_calls_pagination(self, db_session: AsyncSession) -> None:
        cl = CallLogger(db_session)
        for _ in range(5):
            await _log_sample(cl)

        page1 = await cl.get_calls(TENANT, limit=2, offset=0)
        page2 = await cl.get_calls(TENANT, limit=2, offset=2)
        page3 = await cl.get_calls(TENANT, limit=2, offset=4)

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1

        total = await cl.count_calls(TENANT)
        assert total == 5


class TestCompareVersions:
    @pytest.mark.asyncio
    async def test_compare_versions_basic(self, db_session: AsyncSession) -> None:
        cl = CallLogger(db_session)
        await _log_sample(cl, adapter_version="1.0", response_time_ms=100, response_status=200)
        await _log_sample(cl, adapter_version="1.0", response_time_ms=200, response_status=500)
        await _log_sample(cl, adapter_version="2.0", response_time_ms=50, response_status=200)
        await _log_sample(cl, adapter_version="2.0", response_time_ms=60, response_status=200)

        result = await cl.compare_versions(TENANT, "razorpay", "1.0", "2.0")

        assert result["adapter_name"] == "razorpay"
        assert result["version_a"]["total_calls"] == 2
        assert result["version_b"]["total_calls"] == 2
        assert result["version_a"]["error_count"] == 1
        assert result["version_b"]["error_count"] == 0
        assert result["version_b"]["avg_response_time_ms"] == 55.0
