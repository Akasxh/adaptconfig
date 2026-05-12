"""Topological sort + cycle detection for the chain runtime (MVP slice of #109)."""

from __future__ import annotations

import pytest

from finspark.services.chain import (
    ChainCycleError,
    ChainExecutionError,
    topological_sort,
)


class TestLinearChains:
    """A -> B -> C -- the most common shape, OAuth-then-resource-then-confirm."""

    def test_sorts_three_step_linear_chain(self) -> None:
        endpoints = [
            {"id": "C", "path": "/c", "depends_on": "B"},
            {"id": "A", "path": "/a"},
            {"id": "B", "path": "/b", "depends_on": "A"},
        ]
        result = topological_sort(endpoints)
        ids = [ep["id"] for ep in result]
        # Strict order: A must precede B, B must precede C
        assert ids.index("A") < ids.index("B") < ids.index("C")

    def test_single_endpoint_no_deps(self) -> None:
        endpoints = [{"id": "only", "path": "/x"}]
        result = topological_sort(endpoints)
        assert [ep["id"] for ep in result] == ["only"]

    def test_empty_list_returns_empty(self) -> None:
        assert topological_sort([]) == []

    def test_two_independent_roots(self) -> None:
        endpoints = [
            {"id": "X", "path": "/x"},
            {"id": "Y", "path": "/y"},
        ]
        result = topological_sort(endpoints)
        assert {ep["id"] for ep in result} == {"X", "Y"}

    def test_depends_on_as_single_string(self) -> None:
        endpoints = [
            {"id": "B", "path": "/b", "depends_on": "A"},
            {"id": "A", "path": "/a"},
        ]
        result = topological_sort(endpoints)
        assert [ep["id"] for ep in result] == ["A", "B"]

    def test_depends_on_as_list_single_element(self) -> None:
        endpoints = [
            {"id": "B", "path": "/b", "depends_on": ["A"]},
            {"id": "A", "path": "/a"},
        ]
        result = topological_sort(endpoints)
        assert [ep["id"] for ep in result] == ["A", "B"]


class TestBranchingChains:
    """Diamond / fan-out graphs.  Order is partially constrained -- we
    assert the partial order rather than a single canonical sequence."""

    def test_diamond_order_constraints(self) -> None:
        # A -> B,C -> D
        endpoints = [
            {"id": "A", "path": "/a"},
            {"id": "B", "path": "/b", "depends_on": "A"},
            {"id": "C", "path": "/c", "depends_on": "A"},
            {"id": "D", "path": "/d", "depends_on": ["B", "C"]},
        ]
        result = topological_sort(endpoints)
        ids = [ep["id"] for ep in result]
        assert ids.index("A") < ids.index("B")
        assert ids.index("A") < ids.index("C")
        assert ids.index("B") < ids.index("D")
        assert ids.index("C") < ids.index("D")

    def test_fan_out_single_root_many_leaves(self) -> None:
        endpoints = [
            {"id": "ROOT", "path": "/r"},
            {"id": "L1", "path": "/l1", "depends_on": "ROOT"},
            {"id": "L2", "path": "/l2", "depends_on": "ROOT"},
            {"id": "L3", "path": "/l3", "depends_on": "ROOT"},
        ]
        result = topological_sort(endpoints)
        ids = [ep["id"] for ep in result]
        assert ids[0] == "ROOT"
        assert set(ids[1:]) == {"L1", "L2", "L3"}


class TestCycleDetection:
    """Cycles must surface as ChainCycleError -- the route turns these
    into HTTP 400 so the user sees a clear error, not a 500."""

    def test_two_node_cycle_raises_chain_cycle_error(self) -> None:
        endpoints = [
            {"id": "A", "path": "/a", "depends_on": "B"},
            {"id": "B", "path": "/b", "depends_on": "A"},
        ]
        with pytest.raises(ChainCycleError, match="Cycle detected"):
            topological_sort(endpoints)

    def test_three_node_cycle_raises_chain_cycle_error(self) -> None:
        endpoints = [
            {"id": "A", "path": "/a", "depends_on": "C"},
            {"id": "B", "path": "/b", "depends_on": "A"},
            {"id": "C", "path": "/c", "depends_on": "B"},
        ]
        with pytest.raises(ChainCycleError, match="Cycle detected"):
            topological_sort(endpoints)

    def test_self_loop_raises_chain_cycle_error(self) -> None:
        endpoints = [
            {"id": "loop", "path": "/x", "depends_on": "loop"},
        ]
        with pytest.raises(ChainCycleError, match="Cycle detected"):
            topological_sort(endpoints)

    def test_cycle_error_is_chain_execution_error(self) -> None:
        """ChainCycleError must inherit from ChainExecutionError so callers
        catching the base type still catch cycles when they want to."""
        endpoints = [
            {"id": "A", "path": "/a", "depends_on": "B"},
            {"id": "B", "path": "/b", "depends_on": "A"},
        ]
        with pytest.raises(ChainExecutionError):
            topological_sort(endpoints)

    def test_partial_cycle_within_larger_graph(self) -> None:
        # A -> B, C <-> D : two independent roots, one of which cycles
        endpoints = [
            {"id": "A", "path": "/a"},
            {"id": "B", "path": "/b", "depends_on": "A"},
            {"id": "C", "path": "/c", "depends_on": "D"},
            {"id": "D", "path": "/d", "depends_on": "C"},
        ]
        with pytest.raises(ChainCycleError):
            topological_sort(endpoints)


class TestStructuralErrors:
    """Mal-formed graphs surface as ChainExecutionError (NOT cycle errors)."""

    def test_unknown_dependency_raises_chain_execution_error(self) -> None:
        endpoints = [
            {"id": "A", "path": "/a", "depends_on": "GHOST"},
        ]
        with pytest.raises(ChainExecutionError, match="unknown id"):
            topological_sort(endpoints)

    def test_unknown_dependency_is_not_cycle_error(self) -> None:
        """An unknown dep must NOT masquerade as a cycle -- the user gets
        a different (and more useful) error message."""
        endpoints = [
            {"id": "A", "path": "/a", "depends_on": "GHOST"},
        ]
        with pytest.raises(ChainExecutionError) as ei:
            topological_sort(endpoints)
        assert not isinstance(ei.value, ChainCycleError)

    def test_missing_id_raises(self) -> None:
        endpoints = [{"path": "/no-id"}]
        with pytest.raises(ChainExecutionError, match="missing 'id'"):
            topological_sort(endpoints)

    def test_duplicate_id_raises(self) -> None:
        endpoints = [
            {"id": "dup", "path": "/a"},
            {"id": "dup", "path": "/b"},
        ]
        with pytest.raises(ChainExecutionError, match="Duplicate"):
            topological_sort(endpoints)

    def test_non_string_id_raises(self) -> None:
        endpoints = [{"id": 42, "path": "/n"}]
        with pytest.raises(ChainExecutionError, match="must be a string"):
            topological_sort(endpoints)

    def test_non_string_dep_in_list_raises(self) -> None:
        endpoints = [
            {"id": "A", "path": "/a"},
            {"id": "B", "path": "/b", "depends_on": ["A", 7]},
        ]
        with pytest.raises(ChainExecutionError, match="must be string or list"):
            topological_sort(endpoints)
