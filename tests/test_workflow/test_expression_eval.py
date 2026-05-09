"""Tests for the safe expression evaluator."""

import pytest

from finspark.services.orchestration.expression_eval import ExpressionEvaluator


@pytest.fixture
def evaluator() -> ExpressionEvaluator:
    return ExpressionEvaluator()


def test_simple_equality(evaluator: ExpressionEvaluator) -> None:
    ctx = {"x": 1}
    assert evaluator.evaluate("$.context.x == 1", ctx) is True
    assert evaluator.evaluate("$.context.x == 2", ctx) is False


def test_string_comparison(evaluator: ExpressionEvaluator) -> None:
    ctx = {"status": "active"}
    assert evaluator.evaluate("$.context.status == 'active'", ctx) is True
    assert evaluator.evaluate("$.context.status == 'inactive'", ctx) is False


def test_greater_than(evaluator: ExpressionEvaluator) -> None:
    ctx = {"score": 700}
    assert evaluator.evaluate("$.context.score >= 650", ctx) is True
    assert evaluator.evaluate("$.context.score >= 800", ctx) is False
    assert evaluator.evaluate("$.context.score > 699", ctx) is True
    assert evaluator.evaluate("$.context.score > 700", ctx) is False


def test_and_operator(evaluator: ExpressionEvaluator) -> None:
    ctx = {"a": 1, "b": 2}
    assert evaluator.evaluate("$.context.a == 1 AND $.context.b == 2", ctx) is True
    assert evaluator.evaluate("$.context.a == 1 AND $.context.b == 3", ctx) is False


def test_or_operator(evaluator: ExpressionEvaluator) -> None:
    ctx = {"a": 1, "b": 5}
    assert evaluator.evaluate("$.context.a == 1 OR $.context.b == 99", ctx) is True
    assert evaluator.evaluate("$.context.a == 99 OR $.context.b == 5", ctx) is True
    assert evaluator.evaluate("$.context.a == 99 OR $.context.b == 99", ctx) is False


def test_not_equals(evaluator: ExpressionEvaluator) -> None:
    ctx = {"risk": "high"}
    assert evaluator.evaluate("$.context.risk != 'low'", ctx) is True
    assert evaluator.evaluate("$.context.risk != 'high'", ctx) is False


def test_nested_path(evaluator: ExpressionEvaluator) -> None:
    ctx = {"result": {"nested": {"value": True}}}
    assert evaluator.evaluate("$.context.result.nested.value == true", ctx) is True


def test_missing_path_returns_false(evaluator: ExpressionEvaluator) -> None:
    ctx = {"x": 1}
    assert evaluator.evaluate("$.context.missing.path == 1", ctx) is False


def test_less_than(evaluator: ExpressionEvaluator) -> None:
    ctx = {"score": 400}
    assert evaluator.evaluate("$.context.score < 500", ctx) is True
    assert evaluator.evaluate("$.context.score <= 400", ctx) is True


def test_empty_expression_returns_true(evaluator: ExpressionEvaluator) -> None:
    assert evaluator.evaluate("", {}) is True


def test_boolean_literal_false(evaluator: ExpressionEvaluator) -> None:
    ctx = {"active": False}
    assert evaluator.evaluate("$.context.active == false", ctx) is True


def test_resolve_path(evaluator: ExpressionEvaluator) -> None:
    ctx = {"a": {"b": {"c": 42}}}
    assert evaluator.resolve_path("$.context.a.b.c", ctx) == 42
    assert evaluator.resolve_path("$.context.missing", ctx) is None
