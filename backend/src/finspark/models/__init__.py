"""
ORM model registry for the finspark package.

Import everything here so Alembic autogenerate can discover all tables
via Base.metadata.
"""
from finspark.models.base import Base, JSONBType, SoftDeleteMixin, TimestampMixin  # noqa: F401
from finspark.models.adapter import Adapter, AdapterVersion  # noqa: F401
from finspark.models.document import Document  # noqa: F401
from finspark.models.configuration import Configuration, ConfigurationHistory  # noqa: F401
from finspark.models.simulation import Simulation, SimulationStep  # noqa: F401
from finspark.models.audit_log import AuditLog  # noqa: F401

__all__ = [
    "Base",
    "JSONBType",
    "SoftDeleteMixin",
    "TimestampMixin",
    "Adapter",
    "AdapterVersion",
    "Document",
    "Configuration",
    "ConfigurationHistory",
    "Simulation",
    "SimulationStep",
    "AuditLog",
]
