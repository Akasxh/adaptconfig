"""Unit tests for index declarations on SQLAlchemy models.

Verifies that FK columns and high-cardinality filter columns carry explicit
indexes after the refactor that added CASCADE + index=True to every FK.

These tests inspect ORM metadata only — no database connection is required.
The setup_database autouse fixture from the root conftest is overridden with a
no-op so that these purely-static tests are not forced to create/drop tables.
"""

import pytest_asyncio

from finspark.models.adapter import AdapterVersion
from finspark.models.audit import AuditLog
from finspark.models.configuration import Configuration, ConfigurationHistory
from finspark.models.simulation import Simulation, SimulationStep


@pytest_asyncio.fixture(autouse=True)
async def setup_database():  # noqa: PT004
    """No-op override: these tests inspect ORM metadata, not the live database."""
    yield


def _column_is_indexed(model_class, column_name: str) -> bool:
    """Return True when a column is individually indexed (index=True on the column)."""
    col = model_class.__table__.columns[column_name]
    return bool(col.index)


def _column_in_any_index(model_class, column_name: str) -> bool:
    """Return True when a column appears in any table-level Index object."""
    col = model_class.__table__.columns[column_name]
    return any(col in idx.columns for idx in model_class.__table__.indexes)


def _is_indexed(model_class, column_name: str) -> bool:
    """Check both inline column index and table-level Index objects."""
    return _column_is_indexed(model_class, column_name) or _column_in_any_index(
        model_class, column_name
    )


class TestAdapterVersionIndexes:
    def test_adapter_version_adapter_id_is_indexed(self) -> None:
        assert _is_indexed(AdapterVersion, "adapter_id"), (
            "AdapterVersion.adapter_id must be indexed for efficient adapter→versions lookups"
        )


class TestConfigurationIndexes:
    def test_configuration_adapter_version_id_is_indexed(self) -> None:
        assert _is_indexed(Configuration, "adapter_version_id"), (
            "Configuration.adapter_version_id must be indexed"
        )

    def test_configuration_document_id_is_indexed(self) -> None:
        assert _is_indexed(Configuration, "document_id"), (
            "Configuration.document_id must be indexed"
        )


class TestConfigurationHistoryIndexes:
    def test_configuration_history_configuration_id_is_indexed(self) -> None:
        assert _is_indexed(ConfigurationHistory, "configuration_id"), (
            "ConfigurationHistory.configuration_id must be indexed for history lookups"
        )


class TestSimulationIndexes:
    def test_simulation_configuration_id_is_indexed(self) -> None:
        assert _is_indexed(Simulation, "configuration_id"), (
            "Simulation.configuration_id must be indexed"
        )


class TestSimulationStepIndexes:
    def test_simulation_step_simulation_id_is_indexed(self) -> None:
        assert _is_indexed(SimulationStep, "simulation_id"), (
            "SimulationStep.simulation_id must be indexed for step-by-simulation queries"
        )


class TestAuditLogIndexes:
    def test_audit_log_action_is_indexed(self) -> None:
        assert _is_indexed(AuditLog, "action"), (
            "AuditLog.action must be indexed for filtered audit queries"
        )

    def test_audit_log_resource_type_is_indexed(self) -> None:
        assert _is_indexed(AuditLog, "resource_type"), (
            "AuditLog.resource_type must be indexed"
        )

    def test_audit_log_resource_id_is_indexed(self) -> None:
        assert _is_indexed(AuditLog, "resource_id"), (
            "AuditLog.resource_id must be indexed for point lookups on a specific resource"
        )
