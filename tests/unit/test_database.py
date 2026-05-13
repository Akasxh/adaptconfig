"""Unit tests for finspark.core.database.

Verifies that init_db() imports every model module so that all ORM classes are
registered on Base.metadata before create_all() is called.  Each table name
listed here corresponds to a model defined in the finspark.models package.

Each test spins up its own in-memory SQLite engine so these tests are fully
self-contained and do not touch the shared test_finspark.db file.
The setup_database autouse fixture from the root conftest is overridden with a
no-op to avoid interference.
"""

import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from finspark.core.database import init_db
from finspark.models.base import Base


@pytest_asyncio.fixture(autouse=True)
async def setup_database():  # noqa: PT004
    """No-op override: each test in this module manages its own engine."""
    yield

# Use an in-memory SQLite database — no test fixtures required.
_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

EXPECTED_TABLES = {
    "adapters",
    "adapter_versions",
    "documents",
    "configurations",
    "configuration_history",
    "simulations",
    "simulation_steps",
    "audit_logs",
    "webhooks",
    "webhook_deliveries",
    "tenants",
}


class TestInitDb:
    async def test_all_model_tables_registered_after_init_db(self) -> None:
        """init_db() must register every model on Base.metadata before create_all."""
        # Use a dedicated engine so this test is independent of the shared test engine.
        engine = create_async_engine(_TEST_DB_URL, echo=False)

        try:
            # Patch the module-level engine that init_db uses so it targets our
            # in-memory database instead of whatever is in settings.
            import finspark.core.database as db_module

            original_engine = db_module.engine
            db_module.engine = engine

            await init_db()

            registered_tables = set(Base.metadata.tables.keys())
            missing = EXPECTED_TABLES - registered_tables
            assert not missing, (
                f"init_db() did not register the following tables: {sorted(missing)}. "
                "Add the missing model import inside init_db()."
            )
        finally:
            db_module.engine = original_engine
            await engine.dispose()

    async def test_init_db_creates_tables_in_database(self) -> None:
        """init_db() must issue CREATE TABLE statements so tables physically exist."""
        engine = create_async_engine(_TEST_DB_URL, echo=False)

        try:
            import finspark.core.database as db_module

            original_engine = db_module.engine
            db_module.engine = engine

            await init_db()

            # Inspect the live database to confirm the tables were created.
            from sqlalchemy import inspect

            async with engine.connect() as conn:
                table_names = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names()
                )

            created = set(table_names)
            missing = EXPECTED_TABLES - created
            assert not missing, (
                f"The following tables were not created in the database: {sorted(missing)}"
            )
        finally:
            db_module.engine = original_engine
            await engine.dispose()

    async def test_init_db_is_idempotent(self) -> None:
        """Calling init_db() twice must not raise an error (checkfirst semantics)."""
        engine = create_async_engine(_TEST_DB_URL, echo=False)

        try:
            import finspark.core.database as db_module

            original_engine = db_module.engine
            db_module.engine = engine

            # First call
            await init_db()
            # Second call — must not raise
            await init_db()
        finally:
            db_module.engine = original_engine
            await engine.dispose()


class TestExpectedTableList:
    """Sanity checks on the EXPECTED_TABLES constant itself."""

    def test_expected_tables_is_non_empty(self) -> None:
        assert len(EXPECTED_TABLES) > 0

    def test_expected_tables_contains_all_model_groups(self) -> None:
        # Spot-check that each logical model group is represented.
        assert "adapters" in EXPECTED_TABLES
        assert "configurations" in EXPECTED_TABLES
        assert "simulations" in EXPECTED_TABLES
        assert "audit_logs" in EXPECTED_TABLES
        assert "webhooks" in EXPECTED_TABLES
        assert "tenants" in EXPECTED_TABLES
