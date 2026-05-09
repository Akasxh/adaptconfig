"""Tests for the external API audit trail feature."""

import json
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.external_api_audit import ExternalAPIAudit
from finspark.services.audit.external_audit import ExternalAuditService

TENANT_ID = "test-tenant"


def _base_kwargs(**overrides: object) -> dict:
    """Build default kwargs for ExternalAuditService.record()."""
    defaults = {
        "tenant_id": TENANT_ID,
        "user_id": "user-1",
        "configuration_id": "cfg-001",
        "adapter_name": "razorpay",
        "adapter_version": "2.1.0",
        "endpoint_path": "/v1/payments/create",
        "http_method": "POST",
        "request_body": {"amount": 1000, "currency": "INR"},
        "response_status": 200,
        "response_body": {"id": "pay_123", "status": "created"},
        "response_time_ms": 150,
        "success": True,
        "trigger_type": "user_action",
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_record_basic(db_session: AsyncSession) -> None:
    """Create an audit record and verify all fields are stored correctly."""
    svc = ExternalAuditService(db_session)
    record = await svc.record(**_base_kwargs())

    assert record.id is not None
    assert record.tenant_id == TENANT_ID
    assert record.adapter_name == "razorpay"
    assert record.adapter_version == "2.1.0"
    assert record.endpoint_path == "/v1/payments/create"
    assert record.http_method == "POST"
    assert record.response_status == 200
    assert record.response_time_ms == 150
    assert record.success is True
    assert record.trigger_type == "user_action"
    assert record.record_hash != ""
    assert record.previous_hash is None  # first record in chain


@pytest.mark.asyncio
async def test_pii_masking(db_session: AsyncSession) -> None:
    """Aadhaar, PAN, phone, email are masked in stored body."""
    svc = ExternalAuditService(db_session)
    record = await svc.record(**_base_kwargs(
        request_body={
            "aadhaar": "123456789012",
            "pan": "ABCDE1234F",
            "phone": "9876543210",
            "email": "user@example.com",
            "api_key": "secret-key-value",
        },
        response_body={
            "customer_email": "customer@bank.com",
            "customer_phone": "1234567890",
        },
    ))

    req = json.loads(record.request_body_masked)
    assert "XXXX-XXXX-XXXX" in req["aadhaar"]
    assert "XXXXX****X" in req["pan"]
    assert "XXXXXX****" in req["phone"]
    assert "***@***.***" in req["email"]
    assert req["api_key"] == "[REDACTED]"

    resp = json.loads(record.response_body_masked)
    assert "***@***.***" in resp["customer_email"]
    assert "XXXXXX****" in resp["customer_phone"]


@pytest.mark.asyncio
async def test_hash_chain(db_session: AsyncSession) -> None:
    """Create 3 records and verify chain links."""
    svc = ExternalAuditService(db_session)

    r1 = await svc.record(**_base_kwargs())
    r2 = await svc.record(**_base_kwargs(configuration_id="cfg-002"))
    r3 = await svc.record(**_base_kwargs(configuration_id="cfg-003"))

    assert r1.previous_hash is None
    assert r2.previous_hash == r1.record_hash
    assert r3.previous_hash == r2.record_hash

    # All hashes are unique
    hashes = {r1.record_hash, r2.record_hash, r3.record_hash}
    assert len(hashes) == 3


@pytest.mark.asyncio
async def test_chain_verification_valid(db_session: AsyncSession) -> None:
    """verify_chain returns valid for untampered records."""
    svc = ExternalAuditService(db_session)
    await svc.record(**_base_kwargs())
    await svc.record(**_base_kwargs(configuration_id="cfg-002"))
    await svc.record(**_base_kwargs(configuration_id="cfg-003"))

    result = await svc.verify_chain(TENANT_ID)
    assert result["valid"] is True
    assert result["records_checked"] == 3
    assert result["first_broken"] is None


@pytest.mark.asyncio
async def test_chain_verification_broken(db_session: AsyncSession) -> None:
    """Tamper with a hash, verify_chain detects it."""
    svc = ExternalAuditService(db_session)
    await svc.record(**_base_kwargs())
    r2 = await svc.record(**_base_kwargs(configuration_id="cfg-002"))
    await svc.record(**_base_kwargs(configuration_id="cfg-003"))

    # Tamper with r2's hash
    r2.record_hash = "tampered_hash_value"
    await db_session.flush()

    result = await svc.verify_chain(TENANT_ID)
    assert result["valid"] is False
    assert result["first_broken"] == r2.id


@pytest.mark.asyncio
async def test_filter_by_adapter(db_session: AsyncSession) -> None:
    """Filter records by adapter_name."""
    svc = ExternalAuditService(db_session)
    await svc.record(**_base_kwargs(adapter_name="razorpay"))
    await svc.record(**_base_kwargs(adapter_name="stripe"))
    await svc.record(**_base_kwargs(adapter_name="razorpay"))

    records = await svc.get_records(TENANT_ID, adapter_name="razorpay")
    assert len(records) == 2
    assert all(r.adapter_name == "razorpay" for r in records)

    records = await svc.get_records(TENANT_ID, adapter_name="stripe")
    assert len(records) == 1


@pytest.mark.asyncio
async def test_filter_by_success(db_session: AsyncSession) -> None:
    """Filter records by success status."""
    svc = ExternalAuditService(db_session)
    await svc.record(**_base_kwargs(success=True))
    await svc.record(**_base_kwargs(success=False, error_code="TIMEOUT", error_message="Request timed out"))
    await svc.record(**_base_kwargs(success=True))

    successes = await svc.get_records(TENANT_ID, success=True)
    assert len(successes) == 2

    failures = await svc.get_records(TENANT_ID, success=False)
    assert len(failures) == 1
    assert failures[0].error_code == "TIMEOUT"


@pytest.mark.asyncio
async def test_filter_by_date_range(db_session: AsyncSession) -> None:
    """Filter records by date range."""
    svc = ExternalAuditService(db_session)
    await svc.record(**_base_kwargs())
    await svc.record(**_base_kwargs())

    now = datetime.now(UTC)
    future = now + timedelta(hours=1)
    past = now - timedelta(hours=1)

    # All records should be within the last hour
    records = await svc.get_records(TENANT_ID, from_date=past, to_date=future)
    assert len(records) == 2

    # No records in the future
    records = await svc.get_records(TENANT_ID, from_date=future)
    assert len(records) == 0


@pytest.mark.asyncio
async def test_export_json(db_session: AsyncSession) -> None:
    """Export records as JSON."""
    svc = ExternalAuditService(db_session)
    await svc.record(**_base_kwargs())
    await svc.record(**_base_kwargs(adapter_name="stripe"))

    exported = await svc.export_records(TENANT_ID, format="json")
    data = json.loads(exported)
    assert len(data) == 2
    assert all("record_hash" in item for item in data)
    assert all("adapter_name" in item for item in data)
