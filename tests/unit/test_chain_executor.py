"""Unit tests for the chain runtime (services/chain).

Covers the four pieces the persona's acceptance criteria call out explicitly:

* topological sort,
* JSONPath ``extract``,
* ``inject`` into the next request payload,
* cycle detection (raises ``ChainCycleError``),
* and the no-op path for single-endpoint / non-chain configs.

The chain executor runs against the existing :class:`MockAPIServer` so these
tests don't need any HTTP plumbing.
"""
from __future__ import annotations

from typing import Any

import pytest

from finspark.services.chain import (
    ChainCycleError,
    ChainExecutor,
    extract_path,
    is_chain,
)
from finspark.services.chain.executor import topological_sort
from finspark.services.chain.jsonpath import set_path
from finspark.services.simulation.simulator import MockAPIServer


# ---------------------------------------------------------------------------
# is_chain detection
# ---------------------------------------------------------------------------


class TestIsChain:
    def test_empty_endpoints_is_not_a_chain(self) -> None:
        assert is_chain([]) is False
        assert is_chain(None) is False

    def test_single_endpoint_is_not_a_chain(self) -> None:
        assert is_chain([{"id": "a", "path": "/x", "depends_on": []}]) is False

    def test_two_endpoints_no_depends_on_is_not_a_chain(self) -> None:
        endpoints = [
            {"id": "a", "path": "/x"},
            {"id": "b", "path": "/y"},
        ]
        assert is_chain(endpoints) is False

    def test_two_endpoints_with_depends_on_is_a_chain(self) -> None:
        endpoints = [
            {"id": "a", "path": "/x"},
            {"id": "b", "path": "/y", "depends_on": "a"},
        ]
        assert ChainExecutor.is_chain(endpoints) is True


# ---------------------------------------------------------------------------
# Topological sort + cycle detection
# ---------------------------------------------------------------------------


class TestTopologicalSort:
    def test_linear_chain_orders_dependencies_first(self) -> None:
        endpoints = [
            {"id": "c", "path": "/c", "depends_on": ["b"]},
            {"id": "b", "path": "/b", "depends_on": ["a"]},
            {"id": "a", "path": "/a"},
        ]
        ordered = topological_sort(endpoints)
        assert [ep["id"] for ep in ordered] == ["a", "b", "c"]

    def test_diamond_chain_keeps_each_dep_before_dependent(self) -> None:
        endpoints = [
            {"id": "root", "path": "/r"},
            {"id": "left", "path": "/l", "depends_on": ["root"]},
            {"id": "right", "path": "/r2", "depends_on": ["root"]},
            {"id": "join", "path": "/j", "depends_on": ["left", "right"]},
        ]
        ordered_ids = [ep["id"] for ep in topological_sort(endpoints)]
        assert ordered_ids[0] == "root"
        assert ordered_ids[-1] == "join"
        assert ordered_ids.index("left") < ordered_ids.index("join")
        assert ordered_ids.index("right") < ordered_ids.index("join")

    def test_independent_endpoints_keep_original_order(self) -> None:
        endpoints = [
            {"id": "first", "path": "/1"},
            {"id": "second", "path": "/2"},
            {"id": "third", "path": "/3", "depends_on": ["first"]},
        ]
        ordered_ids = [ep["id"] for ep in topological_sort(endpoints)]
        assert ordered_ids == ["first", "second", "third"]

    def test_two_node_cycle_raises(self) -> None:
        endpoints = [
            {"id": "a", "path": "/a", "depends_on": ["b"]},
            {"id": "b", "path": "/b", "depends_on": ["a"]},
        ]
        with pytest.raises(ChainCycleError, match="Cycle detected"):
            topological_sort(endpoints)

    def test_three_node_cycle_raises(self) -> None:
        endpoints = [
            {"id": "a", "path": "/a", "depends_on": ["c"]},
            {"id": "b", "path": "/b", "depends_on": ["a"]},
            {"id": "c", "path": "/c", "depends_on": ["b"]},
        ]
        with pytest.raises(ChainCycleError):
            topological_sort(endpoints)

    def test_self_loop_raises(self) -> None:
        endpoints = [
            {"id": "loop", "path": "/x", "depends_on": ["loop"]},
        ]
        with pytest.raises(ChainCycleError, match="cannot depend on itself"):
            topological_sort(endpoints)

    def test_duplicate_id_raises(self) -> None:
        endpoints = [
            {"id": "dup", "path": "/a"},
            {"id": "dup", "path": "/b", "depends_on": ["dup"]},
        ]
        with pytest.raises(ChainCycleError, match="Duplicate"):
            topological_sort(endpoints)

    def test_unknown_dependency_raises(self) -> None:
        endpoints = [
            {"id": "a", "path": "/a", "depends_on": ["does_not_exist"]},
        ]
        with pytest.raises(ChainCycleError, match="unknown id"):
            topological_sort(endpoints)


# ---------------------------------------------------------------------------
# JSONPath extract / set
# ---------------------------------------------------------------------------


class TestExtractPath:
    def test_dollar_root_returns_input(self) -> None:
        data = {"a": 1}
        assert extract_path(data, "$") is data
        assert extract_path(data, "") is data

    def test_dotted_lookup(self) -> None:
        data = {"access_token": "abc"}
        assert extract_path(data, "$.access_token") == "abc"

    def test_nested_dotted_lookup(self) -> None:
        data = {"data": {"user": {"id": 42}}}
        assert extract_path(data, "$.data.user.id") == 42
        assert extract_path(data, "data.user.id") == 42

    def test_array_index(self) -> None:
        data = {"items": [{"id": "x"}, {"id": "y"}]}
        assert extract_path(data, "$.items[1].id") == "y"

    def test_missing_key_returns_none(self) -> None:
        assert extract_path({"a": 1}, "$.b") is None

    def test_out_of_range_index_returns_none(self) -> None:
        assert extract_path({"x": [1]}, "$.x[5]") is None

    def test_non_string_path_returns_none(self) -> None:
        assert extract_path({"a": 1}, 42) is None  # type: ignore[arg-type]


class TestSetPath:
    def test_writes_top_level_key(self) -> None:
        target: dict[str, Any] = {}
        set_path(target, "access_token", "tok")
        assert target == {"access_token": "tok"}

    def test_creates_intermediate_dicts(self) -> None:
        target: dict[str, Any] = {}
        set_path(target, "headers.Authorization", "Bearer x")
        assert target == {"headers": {"Authorization": "Bearer x"}}

    def test_overwrites_existing(self) -> None:
        target = {"a": "old"}
        set_path(target, "a", "new")
        assert target == {"a": "new"}


# ---------------------------------------------------------------------------
# ChainExecutor.run -- integration with MockAPIServer
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_server() -> MockAPIServer:
    return MockAPIServer()


class TestChainExecutorRun:
    def test_two_step_chain_executes_in_order(self, mock_server: MockAPIServer) -> None:
        endpoints = [
            {"id": "a", "path": "/first", "method": "POST"},
            {"id": "b", "path": "/second", "method": "POST", "depends_on": ["a"]},
        ]
        executor = ChainExecutor(mock_server)

        steps = executor.run(endpoints, config={"adapter_name": "Generic"}, base_request={})

        assert len(steps) == 2
        assert steps[0].step_name.startswith("chain_step_a_")
        assert steps[1].step_name.startswith("chain_step_b_")
        assert steps[0].status == "passed"
        assert steps[1].status == "passed"

    def test_two_step_oauth_then_resource_injects_access_token(
        self, mock_server: MockAPIServer
    ) -> None:
        """The acceptance flow: token endpoint feeds protected endpoint."""

        class FakeMockServer:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, Any]]] = []

            def generate_response(
                self,
                endpoint: dict[str, Any],
                request_payload: dict[str, Any],
                response_schema: dict[str, Any] | None = None,
                config: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                path = endpoint.get("path", "")
                self.calls.append((path, dict(request_payload)))
                if path.endswith("/token"):
                    return {
                        "status": "success",
                        "access_token": "tok-123",
                        "token_type": "Bearer",
                    }
                return {
                    "status": "success",
                    "received_token": request_payload.get("access_token"),
                    "received_auth_header": request_payload.get("headers", {}).get("Authorization"),
                }

        fake = FakeMockServer()
        endpoints = [
            {
                "id": "auth",
                "path": "/oauth/token",
                "method": "POST",
                "extract": [{"name": "access_token", "path": "$.access_token"}],
            },
            {
                "id": "resource",
                "path": "/protected",
                "method": "POST",
                "depends_on": ["auth"],
                "inject": [
                    {"from": "access_token", "to": "access_token"},
                    {"from": "access_token", "to": "headers.Authorization"},
                ],
            },
        ]

        executor = ChainExecutor(fake)  # type: ignore[arg-type]
        steps = executor.run(endpoints, config={}, base_request={})

        assert len(steps) == 2
        assert fake.calls[0][0] == "/oauth/token"
        # Step 2 saw the access_token from step 1
        assert fake.calls[1][1]["access_token"] == "tok-123"
        assert fake.calls[1][1]["headers"]["Authorization"] == "tok-123"
        # And that's reflected in the simulation result for step 2
        assert steps[1].actual_response["received_token"] == "tok-123"
        assert steps[1].actual_response["received_auth_header"] == "tok-123"

    def test_run_raises_on_cycle(self, mock_server: MockAPIServer) -> None:
        endpoints = [
            {"id": "a", "path": "/a", "depends_on": ["b"]},
            {"id": "b", "path": "/b", "depends_on": ["a"]},
        ]
        executor = ChainExecutor(mock_server)
        with pytest.raises(ChainCycleError):
            executor.run(endpoints, config={}, base_request={})

    def test_disabled_endpoint_is_skipped(self, mock_server: MockAPIServer) -> None:
        endpoints = [
            {"id": "a", "path": "/a"},
            {"id": "b", "path": "/b", "depends_on": ["a"], "enabled": False},
        ]
        executor = ChainExecutor(mock_server)
        steps = executor.run(endpoints, config={}, base_request={})
        assert len(steps) == 1
        assert steps[0].step_name.startswith("chain_step_a_")

    def test_inject_with_missing_source_does_not_raise(
        self, mock_server: MockAPIServer
    ) -> None:
        """Best-effort: if ``inject.from`` references something never extracted,
        the chain still runs and just records the inject as not-applied."""
        endpoints = [
            {"id": "a", "path": "/a"},
            {
                "id": "b",
                "path": "/b",
                "depends_on": ["a"],
                "inject": [{"from": "never_extracted", "to": "x"}],
            },
        ]
        executor = ChainExecutor(mock_server)
        steps = executor.run(endpoints, config={}, base_request={})
        # The chain still produced 2 steps; second step's inject just shows applied=False.
        assert len(steps) == 2
        inject_assertion = next(
            a for a in steps[1].assertions if a.get("type") == "chain_inject"
        )
        assert inject_assertion["items"][0]["applied"] is False


# ---------------------------------------------------------------------------
# Single-endpoint no-op behaviour (acceptance: existing single-endpoint
# configs unaffected).
# ---------------------------------------------------------------------------


class TestSingleEndpointNoOp:
    def test_simulator_does_not_route_single_endpoint_through_chain(self) -> None:
        from finspark.services.simulation.simulator import IntegrationSimulator

        simulator = IntegrationSimulator()
        config: dict[str, Any] = {
            "adapter_name": "CIBIL Credit Bureau",
            "version": "v1",
            "base_url": "https://api.cibil.com/v1",
            "auth": {"type": "api_key"},
            "endpoints": [{"id": "only", "path": "/credit-score", "method": "POST"}],
            "field_mappings": [
                {"source_field": "pan_number", "target_field": "pan", "confidence": 1.0}
            ],
            "hooks": [],
        }

        steps = simulator.run_simulation(config, test_type="smoke")
        assert all(not s.step_name.startswith("chain_step_") for s in steps)
        # The legacy per-endpoint test should still appear.
        assert any(s.step_name.startswith("endpoint_test_") for s in steps)
