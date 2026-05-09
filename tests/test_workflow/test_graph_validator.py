"""Tests for workflow graph validator with Tarjan's SCC."""

import pytest

from finspark.services.orchestration.graph_validator import GraphValidator


@pytest.fixture
def validator() -> GraphValidator:
    return GraphValidator()


def test_valid_linear_workflow(validator: GraphValidator) -> None:
    """A->B->C(terminal) should pass validation."""
    definition = {
        "initial_state": "A",
        "nodes": {
            "A": {
                "type": "start",
                "transitions": [{"target": "B"}],
            },
            "B": {
                "type": "transform",
                "transitions": [{"target": "C"}],
            },
            "C": {
                "type": "start",
                "terminal": True,
            },
        },
    }
    result = validator.validate(definition)
    assert result.valid is True
    assert result.errors == []
    assert result.cycles_detected == []
    assert result.unreachable_nodes == []


def test_valid_cycle_with_max_visits(validator: GraphValidator) -> None:
    """A->B->A with max_visits on A should pass."""
    definition = {
        "initial_state": "A",
        "nodes": {
            "A": {
                "type": "transform",
                "max_visits": 3,
                "on_max_visits": "C",
                "transitions": [{"target": "B"}],
            },
            "B": {
                "type": "transform",
                "transitions": [{"target": "A"}],
            },
            "C": {
                "type": "start",
                "terminal": True,
            },
        },
    }
    result = validator.validate(definition)
    assert result.valid is True
    assert len(result.cycles_detected) == 1
    assert set(result.cycles_detected[0]) == {"A", "B"}


def test_invalid_cycle_no_max_visits(validator: GraphValidator) -> None:
    """A->B->A without max_visits should fail."""
    definition = {
        "initial_state": "A",
        "nodes": {
            "A": {
                "type": "transform",
                "transitions": [{"target": "B"}],
            },
            "B": {
                "type": "transform",
                "transitions": [{"target": "A"}],
            },
            "C": {
                "type": "start",
                "terminal": True,
            },
        },
    }
    result = validator.validate(definition)
    assert result.valid is False
    assert any("no node with max_visits" in e for e in result.errors)


def test_unreachable_node_detected(validator: GraphValidator) -> None:
    """Nodes not reachable from initial_state should be warned about."""
    definition = {
        "initial_state": "A",
        "nodes": {
            "A": {
                "type": "start",
                "transitions": [{"target": "B"}],
            },
            "B": {
                "type": "start",
                "terminal": True,
            },
            "orphan": {
                "type": "transform",
                "transitions": [],
            },
        },
    }
    result = validator.validate(definition)
    assert result.valid is True
    assert "orphan" in result.unreachable_nodes
    assert any("Unreachable" in w for w in result.warnings)


def test_missing_initial_state(validator: GraphValidator) -> None:
    """Missing initial_state should fail."""
    definition = {
        "nodes": {
            "A": {"type": "start", "terminal": True},
        },
    }
    result = validator.validate(definition)
    assert result.valid is False
    assert any("initial_state" in e.lower() for e in result.errors)


def test_no_terminal_node(validator: GraphValidator) -> None:
    """No terminal node should fail."""
    definition = {
        "initial_state": "A",
        "nodes": {
            "A": {
                "type": "start",
                "transitions": [{"target": "B"}],
            },
            "B": {
                "type": "transform",
                "transitions": [],
            },
        },
    }
    result = validator.validate(definition)
    assert result.valid is False
    assert any("terminal" in e.lower() for e in result.errors)


def test_transition_to_nonexistent_node(validator: GraphValidator) -> None:
    """Transition to nonexistent node should fail."""
    definition = {
        "initial_state": "A",
        "nodes": {
            "A": {
                "type": "start",
                "transitions": [{"target": "MISSING"}],
            },
            "B": {
                "type": "start",
                "terminal": True,
            },
        },
    }
    result = validator.validate(definition)
    assert result.valid is False
    assert any("non-existent" in e for e in result.errors)
