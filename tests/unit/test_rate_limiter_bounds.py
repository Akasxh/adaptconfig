"""Tests for rate limiter bounded collections and path normalization."""

import pytest

from finspark.core.rate_limiter import (
    MetricsCollector,
    _normalize_path,
    _TokenBucket,
)


class TestNormalizePath:
    def test_replaces_uuid_in_path(self) -> None:
        path = "/api/v1/documents/550e8400-e29b-41d4-a716-446655440000/detail"
        assert _normalize_path(path) == "/api/v1/documents/{id}/detail"

    def test_replaces_multiple_uuids(self) -> None:
        path = "/api/v1/configs/550e8400-e29b-41d4-a716-446655440000/compare/660e8400-e29b-41d4-a716-446655440001"
        result = _normalize_path(path)
        assert "{id}" in result
        assert result.count("{id}") == 2

    def test_no_uuid_unchanged(self) -> None:
        path = "/api/v1/health"
        assert _normalize_path(path) == "/api/v1/health"

    def test_uppercase_uuid(self) -> None:
        path = "/api/v1/docs/550E8400-E29B-41D4-A716-446655440000"
        assert _normalize_path(path) == "/api/v1/docs/{id}"


class TestTokenBucketBounded:
    @pytest.mark.asyncio
    async def test_evicts_oldest_tenant_at_capacity(self) -> None:
        bucket = _TokenBucket(max_requests=100, window_seconds=60)
        # Manually set a low cap for testing by filling beyond it
        from finspark.core import rate_limiter as rl_mod

        original = rl_mod._MAX_TENANTS
        rl_mod._MAX_TENANTS = 3
        try:
            await bucket.is_allowed("t1")
            await bucket.is_allowed("t2")
            await bucket.is_allowed("t3")
            # t1 is oldest, should be evicted when t4 arrives
            await bucket.is_allowed("t4")
            async with bucket._lock:
                assert "t1" not in bucket._requests
                assert "t4" in bucket._requests
                assert len(bucket._requests) <= 3
        finally:
            rl_mod._MAX_TENANTS = original

    @pytest.mark.asyncio
    async def test_uses_ordered_dict(self) -> None:
        bucket = _TokenBucket(max_requests=5, window_seconds=60)
        from collections import OrderedDict

        assert isinstance(bucket._requests, OrderedDict)


class TestMetricsCollectorBounded:
    @pytest.mark.asyncio
    async def test_normalizes_uuid_paths(self) -> None:
        m = MetricsCollector()
        await m.record(
            "/api/v1/docs/550e8400-e29b-41d4-a716-446655440000", "t1", 10.0
        )
        await m.record(
            "/api/v1/docs/660e8400-e29b-41d4-a716-446655440001", "t1", 10.0
        )
        snap = await m.snapshot()
        # Both should map to the same normalized path
        assert snap["requests_per_endpoint"]["/api/v1/docs/{id}"] == 2

    @pytest.mark.asyncio
    async def test_active_tenants_capped(self) -> None:
        m = MetricsCollector()
        from finspark.core import rate_limiter as rl_mod

        original = rl_mod._MAX_TENANTS
        rl_mod._MAX_TENANTS = 3
        try:
            for i in range(5):
                await m.record("/api/v1/health", f"tenant-{i}", 1.0)
            snap = await m.snapshot()
            assert snap["active_tenants"] <= 3
        finally:
            rl_mod._MAX_TENANTS = original

    @pytest.mark.asyncio
    async def test_active_tenants_uses_ordered_dict(self) -> None:
        m = MetricsCollector()
        from collections import OrderedDict

        assert isinstance(m.active_tenants, OrderedDict)
