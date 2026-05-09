"""Workflow orchestration models."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from finspark.models.base import Base, TenantMixin, TimestampMixin, UUIDMixin


class Workflow(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Workflow definition with graph topology."""

    __tablename__ = "workflows"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(50), default="1.0")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    definition: Mapped[str] = mapped_column(Text, nullable=False)  # JSON: nodes, transitions
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=86400)
    max_total_steps: Mapped[int] = mapped_column(Integer, default=500)
    fuel_budget: Mapped[int] = mapped_column(Integer, default=1000)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | archived


class WorkflowRun(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Instance of a running workflow."""

    __tablename__ = "workflow_runs"

    workflow_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    current_node: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    context: Mapped[str] = mapped_column(Text, default="{}")  # JSON: accumulated data
    visit_counts: Mapped[str] = mapped_column(Text, default="{}")  # JSON: {node_id: count}
    fuel_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    steps_taken: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    callback_url: Mapped[str | None] = mapped_column(String(500), nullable=True)


class WorkflowStepLog(Base, UUIDMixin, TimestampMixin):
    """Audit log for each step executed in a workflow run."""

    __tablename__ = "workflow_step_logs"

    run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # success|failed|skipped
    input_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    output_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    transition_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
