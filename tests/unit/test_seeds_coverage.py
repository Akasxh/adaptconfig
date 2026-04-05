"""Tests for seed data loader to boost coverage."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from finspark.seeds import _load_seed_data, seed_adapters


class TestLoadSeedData:
    def test_load_seed_data_returns_list(self) -> None:
        data = _load_seed_data()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_seed_data_has_required_fields(self) -> None:
        data = _load_seed_data()
        for adapter in data:
            assert "name" in adapter
            assert "category" in adapter
            assert "versions" in adapter


class TestSeedAdapters:
    @pytest.mark.asyncio
    async def test_seed_adapters_skips_if_already_seeded(self) -> None:
        """When adapters exist, seeding is skipped."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = MagicMock()  # Adapter exists
        mock_db.execute.return_value = mock_result

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("finspark.seeds.async_session_factory", return_value=mock_session_ctx):
            await seed_adapters()
            # Should have called execute once (the check query) but not commit
            mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_adapters_inserts_when_empty(
        self, db_session: "AsyncSession",  # noqa: F821
    ) -> None:
        """When no adapters exist, seeding runs."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession

        from finspark.models.adapter import Adapter

        # Verify empty first
        result = await db_session.execute(select(Adapter).limit(1))
        assert result.scalar_one_or_none() is None

        # Use the test session factory instead of the real one
        from tests.conftest import test_session_factory

        with patch("finspark.seeds.async_session_factory", test_session_factory):
            await seed_adapters()

        # Check adapters were created
        result = await db_session.execute(select(Adapter))
        adapters = result.scalars().all()
        assert len(adapters) > 0
