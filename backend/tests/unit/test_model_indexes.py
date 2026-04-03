"""
Tests that verify index, FK ondelete, and relationship metadata on finspark ORM models.

These are static metadata checks — no DB round-trip required.
"""
from __future__ import annotations

import pytest
from sqlalchemy import inspect as sa_inspect

from finspark.models import (
    AdapterVersion,
    AuditLog,
    Configuration,
    ConfigurationHistory,
    Simulation,
    SimulationStep,
)
from finspark.models.base import Base


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _indexed_columns(model_cls: type) -> set[str]:
    """Return column names that have an index (inline or via __table_args__)."""
    table = model_cls.__table__
    indexed: set[str] = set()

    # Columns with index=True
    for col in table.columns:
        if col.index:
            indexed.add(col.name)

    # Explicit Index objects in __table_args__
    for idx in table.indexes:
        for col in idx.columns:
            indexed.add(col.name)

    return indexed


def _fk_ondelete(model_cls: type, column_name: str) -> str | None:
    """Return the ondelete rule for a FK column, or None if not a FK."""
    table = model_cls.__table__
    col = table.columns[column_name]
    for fk in col.foreign_keys:
        return fk.ondelete
    return None


def _relationship_names(model_cls: type) -> set[str]:
    """Return the set of relationship names declared on a mapper."""
    mapper = sa_inspect(model_cls)
    return {r.key for r in mapper.relationships}


# ---------------------------------------------------------------------------
# AdapterVersion
# ---------------------------------------------------------------------------


class TestAdapterVersionIndexes:
    def test_adapter_id_is_indexed(self) -> None:
        assert "adapter_id" in _indexed_columns(AdapterVersion)

    def test_status_is_indexed(self) -> None:
        assert "status" in _indexed_columns(AdapterVersion)

    def test_adapter_id_fk_ondelete_restrict(self) -> None:
        assert _fk_ondelete(AdapterVersion, "adapter_id") == "RESTRICT"

    def test_has_adapter_relationship(self) -> None:
        assert "adapter" in _relationship_names(AdapterVersion)

    def test_has_configurations_relationship(self) -> None:
        assert "configurations" in _relationship_names(AdapterVersion)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfigurationIndexes:
    def test_status_is_indexed(self) -> None:
        assert "status" in _indexed_columns(Configuration)

    def test_adapter_version_id_is_indexed(self) -> None:
        assert "adapter_version_id" in _indexed_columns(Configuration)

    def test_tenant_id_is_indexed(self) -> None:
        assert "tenant_id" in _indexed_columns(Configuration)

    def test_adapter_version_id_fk_ondelete_restrict(self) -> None:
        assert _fk_ondelete(Configuration, "adapter_version_id") == "RESTRICT"

    def test_has_adapter_version_relationship(self) -> None:
        assert "adapter_version" in _relationship_names(Configuration)

    def test_has_history_relationship(self) -> None:
        assert "history" in _relationship_names(Configuration)

    def test_has_simulations_relationship(self) -> None:
        assert "simulations" in _relationship_names(Configuration)


# ---------------------------------------------------------------------------
# ConfigurationHistory
# ---------------------------------------------------------------------------


class TestConfigurationHistoryIndexes:
    def test_configuration_id_is_indexed(self) -> None:
        assert "configuration_id" in _indexed_columns(ConfigurationHistory)

    def test_configuration_id_fk_ondelete_cascade(self) -> None:
        assert _fk_ondelete(ConfigurationHistory, "configuration_id") == "CASCADE"

    def test_has_configuration_relationship(self) -> None:
        assert "configuration" in _relationship_names(ConfigurationHistory)


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


class TestSimulationIndexes:
    def test_configuration_id_is_indexed(self) -> None:
        assert "configuration_id" in _indexed_columns(Simulation)

    def test_tenant_id_is_indexed(self) -> None:
        assert "tenant_id" in _indexed_columns(Simulation)

    def test_status_is_indexed(self) -> None:
        assert "status" in _indexed_columns(Simulation)

    def test_configuration_id_fk_ondelete_cascade(self) -> None:
        assert _fk_ondelete(Simulation, "configuration_id") == "CASCADE"

    def test_has_configuration_relationship(self) -> None:
        assert "configuration" in _relationship_names(Simulation)

    def test_has_steps_relationship(self) -> None:
        assert "steps" in _relationship_names(Simulation)


# ---------------------------------------------------------------------------
# SimulationStep
# ---------------------------------------------------------------------------


class TestSimulationStepIndexes:
    def test_simulation_id_is_indexed(self) -> None:
        assert "simulation_id" in _indexed_columns(SimulationStep)

    def test_simulation_id_fk_ondelete_cascade(self) -> None:
        assert _fk_ondelete(SimulationStep, "simulation_id") == "CASCADE"

    def test_has_simulation_relationship(self) -> None:
        assert "simulation" in _relationship_names(SimulationStep)


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


class TestAuditLogIndexes:
    def test_action_is_indexed(self) -> None:
        assert "action" in _indexed_columns(AuditLog)

    def test_resource_type_is_indexed(self) -> None:
        assert "resource_type" in _indexed_columns(AuditLog)

    def test_tenant_id_is_indexed(self) -> None:
        assert "tenant_id" in _indexed_columns(AuditLog)


# ---------------------------------------------------------------------------
# __repr__ smoke tests (no DB needed)
# ---------------------------------------------------------------------------


class TestRepr:
    def test_adapter_version_repr(self) -> None:
        obj = AdapterVersion(
            id="abc",
            adapter_id="def",
            semver="1.0.0",
            status="published",
        )
        r = repr(obj)
        assert "AdapterVersion" in r
        assert "1.0.0" in r

    def test_configuration_repr(self) -> None:
        obj = Configuration(
            id="abc",
            tenant_id="t1",
            status="draft",
            version=1,
        )
        r = repr(obj)
        assert "Configuration" in r
        assert "draft" in r

    def test_simulation_repr(self) -> None:
        obj = Simulation(
            id="abc",
            configuration_id="cfg",
            scenario="smoke",
            status="passed",
        )
        r = repr(obj)
        assert "Simulation" in r
        assert "passed" in r

    def test_audit_log_repr(self) -> None:
        obj = AuditLog(
            id="abc",
            action="configuration.created",
            resource_type="Configuration",
            resource_id="x",
        )
        r = repr(obj)
        assert "AuditLog" in r
        assert "configuration.created" in r


# ---------------------------------------------------------------------------
# All models registered in Base.metadata
# ---------------------------------------------------------------------------


def test_all_finspark_tables_in_metadata() -> None:
    expected = {
        "finspark_adapters",
        "finspark_adapter_versions",
        "finspark_documents",
        "finspark_configurations",
        "finspark_configuration_history",
        "finspark_simulations",
        "finspark_simulation_steps",
        "finspark_audit_logs",
    }
    actual = set(Base.metadata.tables.keys())
    assert expected <= actual, f"Missing tables: {expected - actual}"
