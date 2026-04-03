"""
Simulation and SimulationStep ORM models.

Simulation     — a sandboxed test run against a Configuration.
SimulationStep — individual step trace within a simulation run.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from finspark.models.base import Base, JSONBType, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from finspark.models.configuration import Configuration


class Simulation(TimestampMixin, Base):
    """
    Sandboxed test run against a specific Configuration.

    Immutable after completion — never updated once status reaches a terminal state.
    """

    __tablename__ = "finspark_simulations"

    id: Mapped[str] = uuid_pk()
    tenant_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )
    configuration_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("finspark_configurations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scenario: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="default",
        comment="Named test scenario",
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="queued",
        comment="queued | running | passed | failed | cancelled | timed_out",
    )
    # Timing
    queued_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    started_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Aggregate counts
    pass_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Request overrides applied for this run
    payload_override: Mapped[dict[str, Any]] = mapped_column(
        JSONBType(),
        nullable=False,
        default=dict,
        server_default="{}",
    )
    assertions: Mapped[list] = mapped_column(
        JSONBType(),
        nullable=False,
        default=list,
        server_default="[]",
        comment="AssertionSpec list serialized as JSON",
    )
    mock_external: Mapped[bool] = mapped_column(nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Relationships
    configuration: Mapped["Configuration"] = relationship(
        "Configuration",
        back_populates="simulations",
        lazy="noload",
    )
    steps: Mapped[list["SimulationStep"]] = relationship(
        "SimulationStep",
        back_populates="simulation",
        lazy="noload",
        order_by="SimulationStep.created_at",
    )

    __table_args__ = (
        Index("ix_finspark_simulations_tenant_id", "tenant_id"),
        Index("ix_finspark_simulations_configuration_id", "configuration_id"),
        Index("ix_finspark_simulations_status", "status"),
        Index(
            "ix_finspark_simulations_tenant_status",
            "tenant_id",
            "status",
        ),
        CheckConstraint(
            "status IN ('queued','running','passed','failed','cancelled','timed_out')",
            name="ck_finspark_simulations_status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Simulation id={self.id} config={self.configuration_id} "
            f"scenario={self.scenario!r} status={self.status}>"
        )


class SimulationStep(TimestampMixin, Base):
    """Individual step trace within a Simulation run."""

    __tablename__ = "finspark_simulation_steps"

    id: Mapped[str] = uuid_pk()
    simulation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("finspark_simulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_name: Mapped[str] = mapped_column(String(255), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        comment="pending | running | passed | failed | skipped",
    )
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONBType(), nullable=True
    )
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONBType(), nullable=True
    )
    assertion_failures: Mapped[list] = mapped_column(
        JSONBType(),
        nullable=False,
        default=list,
        server_default="[]",
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    simulation: Mapped["Simulation"] = relationship(
        "Simulation",
        back_populates="steps",
        lazy="noload",
    )

    __table_args__ = (
        Index("ix_finspark_simulation_steps_simulation_id", "simulation_id"),
        Index("ix_finspark_simulation_steps_status", "status"),
        CheckConstraint(
            "status IN ('pending','running','passed','failed','skipped')",
            name="ck_finspark_simulation_steps_status",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<SimulationStep id={self.id} simulation={self.simulation_id} "
            f"step={self.step_name!r} status={self.status}>"
        )
