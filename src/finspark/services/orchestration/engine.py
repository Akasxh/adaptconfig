"""Core workflow orchestration engine.

State machine executor supporting arbitrary graph topologies
including cycles with fuel-based termination guarantees.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.models.workflow import Workflow, WorkflowRun, WorkflowStepLog
from finspark.services.orchestration.expression_eval import ExpressionEvaluator
from finspark.services.orchestration.graph_validator import (
    GraphValidationResult,
    GraphValidator,
)

logger = logging.getLogger(__name__)


@dataclass
class NodeResult:
    status: str  # success | failed | paused
    output: dict[str, Any] | None = None
    error: str | None = None
    next_node: str | None = None


class WorkflowEngine:
    """Execute workflow graphs with cycle-safe fuel budgets."""

    FUEL_COSTS: dict[str, int] = {
        "api_call": 10,
        "parallel": 5,
        "wait": 1,
        "transform": 1,
        "condition": 1,
        "compensate": 5,
        "sub_workflow": 10,
    }

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._evaluator = ExpressionEvaluator()
        self._validator = GraphValidator()

    # -- public API --

    async def create_workflow(
        self,
        tenant_id: str,
        name: str,
        definition: dict[str, Any],
        version: str = "1.0",
        description: str | None = None,
        timeout_seconds: int = 86400,
        max_total_steps: int = 500,
        fuel_budget: int = 1000,
    ) -> Workflow:
        """Validate definition and persist workflow."""
        result = self._validator.validate(definition)
        if not result.valid:
            raise ValueError(
                f"Invalid workflow definition: {'; '.join(result.errors)}"
            )

        wf = Workflow(
            tenant_id=tenant_id,
            name=name,
            version=version,
            description=description,
            definition=json.dumps(definition),
            timeout_seconds=timeout_seconds,
            max_total_steps=max_total_steps,
            fuel_budget=fuel_budget,
        )
        self.session.add(wf)
        await self.session.flush()
        return wf

    async def start_run(
        self,
        workflow_id: str,
        tenant_id: str,
        initial_context: dict[str, Any] | None = None,
        callback_url: str | None = None,
    ) -> WorkflowRun:
        """Create a WorkflowRun and begin execution."""
        wf = await self._load_workflow(workflow_id, tenant_id)
        if wf is None:
            raise ValueError(f"Workflow '{workflow_id}' not found")

        definition = json.loads(wf.definition)
        initial_state = definition["initial_state"]

        run = WorkflowRun(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            current_node=initial_state,
            status="created",
            context=json.dumps(initial_context or {}),
            visit_counts=json.dumps({}),
            fuel_remaining=wf.fuel_budget,
            started_at=datetime.now(UTC),
            callback_url=callback_url,
        )
        self.session.add(run)
        await self.session.flush()

        return await self.execute_run(run, wf)

    async def execute_run(
        self, run: WorkflowRun, workflow: Workflow,
    ) -> WorkflowRun:
        """Main execution loop."""
        definition = json.loads(workflow.definition)
        run.status = "running"

        while True:
            # Safety checks
            stop = self._check_limits(run, workflow)
            if stop:
                run.status = stop[0]
                run.terminal_reason = stop[1]
                run.completed_at = datetime.now(UTC)
                await self.session.flush()
                return run

            node_id = run.current_node
            node_def = definition["nodes"].get(node_id)

            if node_def is None:
                run.status = "failed"
                run.terminal_reason = f"Node '{node_id}' not found"
                run.completed_at = datetime.now(UTC)
                await self.session.flush()
                return run

            # Check terminal
            if node_def.get("terminal", False):
                run = await self._complete_run(run, node_id, node_def)
                return run

            # Check + enforce max_visits
            visit_result = self._check_max_visits(run, node_id, node_def)
            if visit_result is not None:
                run.current_node = visit_result
                continue

            # Execute the node
            result = await self._execute_node(run, node_id, node_def)
            run = self._deduct_fuel(run, node_def)
            run.steps_taken += 1

            await self._log_step(run, node_id, node_def, result)

            if result.status == "paused":
                run.status = "paused"
                await self.session.flush()
                return run

            if result.status == "failed":
                run.status = "failed"
                run.terminal_reason = result.error or "Node execution failed"
                run.completed_at = datetime.now(UTC)
                await self.session.flush()
                return run

            # Merge output into context
            if result.output:
                ctx = json.loads(run.context)
                ctx.update(result.output)
                run.context = json.dumps(ctx)

            # Evaluate transitions
            next_node = result.next_node
            if next_node is None:
                transitions = node_def.get("transitions", [])
                ctx = json.loads(run.context)
                next_node = self._evaluate_transitions(transitions, ctx)

            if next_node is None:
                run.status = "completed"
                run.terminal_reason = "No transition matched"
                run.completed_at = datetime.now(UTC)
                await self.session.flush()
                return run

            # Increment visit count for current node
            self._increment_visit(run, node_id)
            run.current_node = next_node
            await self.session.flush()

    async def resume_run(
        self,
        run_id: str,
        tenant_id: str,
        event_data: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        """Resume a paused workflow run."""
        run = await self._load_run(run_id, tenant_id)
        if run is None:
            raise ValueError(f"Run '{run_id}' not found")
        if run.status != "paused":
            raise ValueError(
                f"Cannot resume run in '{run.status}' state"
            )

        # Merge event data into context
        if event_data:
            ctx = json.loads(run.context)
            ctx.update(event_data)
            run.context = json.dumps(ctx)

        # Advance past the wait node
        wf = await self._load_workflow(run.workflow_id, tenant_id)
        if wf is None:
            raise ValueError("Workflow not found for run")

        definition = json.loads(wf.definition)
        node_def = definition["nodes"].get(run.current_node, {})
        transitions = node_def.get("transitions", [])
        ctx = json.loads(run.context)
        next_node = self._evaluate_transitions(transitions, ctx)

        if next_node:
            self._increment_visit(run, run.current_node)
            run.current_node = next_node

        return await self.execute_run(run, wf)

    async def get_run_status(
        self, run_id: str, tenant_id: str,
    ) -> WorkflowRun:
        """Get current state of a run."""
        run = await self._load_run(run_id, tenant_id)
        if run is None:
            raise ValueError(f"Run '{run_id}' not found")
        return run

    async def get_step_logs(
        self, run_id: str,
    ) -> list[WorkflowStepLog]:
        """Get step logs for a run."""
        stmt = (
            select(WorkflowStepLog)
            .where(WorkflowStepLog.run_id == run_id)
            .order_by(WorkflowStepLog.created_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # -- node executors --

    async def _execute_node(
        self,
        run: WorkflowRun,
        node_id: str,
        node_def: dict[str, Any],
    ) -> NodeResult:
        """Dispatch execution by node type."""
        node_type = node_def.get("type", "")
        ctx = json.loads(run.context)

        if node_type == "api_call":
            return await self._execute_api_call(run, node_def)
        if node_type == "transform":
            return self._execute_transform(ctx, node_def)
        if node_type == "condition":
            return self._execute_condition(ctx, node_def)
        if node_type == "wait":
            return self._execute_wait(node_def)
        if node_type == "start":
            return NodeResult(status="success")

        return NodeResult(
            status="failed",
            error=f"Unknown node type: {node_type}",
        )

    async def _execute_api_call(
        self,
        run: WorkflowRun,
        node_def: dict[str, Any],
    ) -> NodeResult:
        """Execute an API call node via ProxyRouter."""
        try:
            from finspark.services.proxy.router import ProxyRouter

            config_id = node_def.get("config_id", "")
            endpoint = node_def.get("endpoint", "/")
            method = node_def.get("method", "POST")
            ctx = json.loads(run.context)
            body = node_def.get("request_body") or ctx

            router = ProxyRouter(self.session)
            result = await router.proxy_request(
                config_id=config_id,
                endpoint_path=endpoint,
                tenant_id=run.tenant_id,
                request_body=body,
                request_method=method,
            )

            output_key = node_def.get("output_key", "api_result")
            output = {
                output_key: {
                    "status_code": result.status_code,
                    "body": result.response_body,
                    "success": result.success,
                },
            }
            status = "success" if result.success else "failed"
            error = result.error if not result.success else None
            return NodeResult(status=status, output=output, error=error)
        except Exception as exc:
            return NodeResult(status="failed", error=str(exc))

    def _execute_transform(
        self,
        ctx: dict[str, Any],
        node_def: dict[str, Any],
    ) -> NodeResult:
        """Execute a transform node."""
        try:
            from finspark.services.transformation.engine import (
                TransformationEngine,
            )

            engine = TransformationEngine()
            mappings = node_def.get("field_mappings", [])
            source_key = node_def.get("source_key")
            source = ctx.get(source_key, ctx) if source_key else ctx

            result = engine.transform(source, mappings)
            output_key = node_def.get("output_key", "transform_result")
            return NodeResult(
                status="success",
                output={output_key: result.payload},
            )
        except Exception as exc:
            return NodeResult(status="failed", error=str(exc))

    def _execute_condition(
        self,
        ctx: dict[str, Any],
        node_def: dict[str, Any],
    ) -> NodeResult:
        """Evaluate condition and select target branch."""
        branches = node_def.get("branches", [])
        for branch in branches:
            condition = branch.get("condition", "")
            target = branch.get("target")
            if self._evaluator.evaluate(condition, ctx):
                return NodeResult(
                    status="success",
                    next_node=target,
                )

        # Default branch
        default = node_def.get("default")
        if default:
            return NodeResult(status="success", next_node=default)

        return NodeResult(
            status="failed",
            error="No condition branch matched",
        )

    @staticmethod
    def _execute_wait(node_def: dict[str, Any]) -> NodeResult:
        """Pause the workflow at a wait node."""
        return NodeResult(status="paused")

    # -- transition evaluation --

    def _evaluate_transitions(
        self,
        transitions: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> str | None:
        """Evaluate transition conditions, return first matching target."""
        for tr in transitions:
            condition = tr.get("condition")
            target = tr.get("target")

            if condition is None or condition == "":
                return target

            if self._evaluator.evaluate(condition, context):
                return target

        return None

    # -- state helpers --

    @staticmethod
    def _check_limits(
        run: WorkflowRun, workflow: Workflow,
    ) -> tuple[str, str] | None:
        """Check fuel and step limits."""
        if run.fuel_remaining <= 0:
            return ("failed", "Fuel budget exhausted")
        if run.steps_taken >= workflow.max_total_steps:
            return ("failed", "Max total steps exceeded")
        return None

    @staticmethod
    def _check_max_visits(
        run: WorkflowRun,
        node_id: str,
        node_def: dict[str, Any],
    ) -> str | None:
        """Check if node has exceeded max_visits. Returns redirect node or None."""
        max_visits = node_def.get("max_visits")
        if max_visits is None:
            return None

        visits = json.loads(run.visit_counts)
        count = visits.get(node_id, 0)
        if count >= max_visits:
            redirect = node_def.get("on_max_visits")
            if redirect:
                return redirect
            return None
        return None

    @staticmethod
    def _increment_visit(run: WorkflowRun, node_id: str) -> None:
        """Increment visit count for a node."""
        visits = json.loads(run.visit_counts)
        visits[node_id] = visits.get(node_id, 0) + 1
        run.visit_counts = json.dumps(visits)

    @staticmethod
    def _deduct_fuel(
        run: WorkflowRun, node_def: dict[str, Any],
    ) -> WorkflowRun:
        """Deduct fuel cost for the executed node."""
        node_type = node_def.get("type", "")
        cost = WorkflowEngine.FUEL_COSTS.get(node_type, 1)
        run.fuel_remaining -= cost
        return run

    async def _complete_run(
        self,
        run: WorkflowRun,
        node_id: str,
        node_def: dict[str, Any],
    ) -> WorkflowRun:
        """Mark run as completed at terminal node."""
        self._increment_visit(run, node_id)
        run.status = "completed"
        run.terminal_reason = f"Reached terminal node '{node_id}'"
        run.completed_at = datetime.now(UTC)
        await self.session.flush()
        return run

    async def _log_step(
        self,
        run: WorkflowRun,
        node_id: str,
        node_def: dict[str, Any],
        result: NodeResult,
    ) -> None:
        """Persist a step log entry."""
        log = WorkflowStepLog(
            run_id=run.id,
            node_id=node_id,
            node_type=node_def.get("type", "unknown"),
            status=result.status,
            input_snapshot=run.context,
            output_snapshot=json.dumps(result.output) if result.output else None,
            error=result.error,
            transition_to=result.next_node,
        )
        self.session.add(log)

    # -- DB loaders --

    async def _load_workflow(
        self, workflow_id: str, tenant_id: str,
    ) -> Workflow | None:
        """Load workflow by id and tenant."""
        stmt = select(Workflow).where(
            Workflow.id == workflow_id,
            Workflow.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_run(
        self, run_id: str, tenant_id: str,
    ) -> WorkflowRun | None:
        """Load run by id and tenant."""
        stmt = select(WorkflowRun).where(
            WorkflowRun.id == run_id,
            WorkflowRun.tenant_id == tenant_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
