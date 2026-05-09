"""Tests for the expression sandbox."""

import pytest

from finspark.services.transformation.sandbox import ExpressionSandbox, UnsafeExpressionError


@pytest.fixture
def sandbox() -> ExpressionSandbox:
    return ExpressionSandbox()


class TestSafeExpressions:
    def test_string_upper(self, sandbox: ExpressionSandbox) -> None:
        assert sandbox.evaluate("value.upper()", "hello") == "HELLO"

    def test_string_split(self, sandbox: ExpressionSandbox) -> None:
        assert sandbox.evaluate("value.split(',')[0]", "a,b,c") == "a"

    def test_arithmetic(self, sandbox: ExpressionSandbox) -> None:
        assert sandbox.evaluate("value * 100", 5) == 500

    def test_string_formatting(self, sandbox: ExpressionSandbox) -> None:
        assert sandbox.evaluate("f'+91{value}'", "9876543210") == "+919876543210"

    def test_context_access(self, sandbox: ExpressionSandbox) -> None:
        result = sandbox.evaluate(
            "context['other_field']",
            "ignored",
            context={"other_field": "from_context"},
        )
        assert result == "from_context"

    def test_builtin_len(self, sandbox: ExpressionSandbox) -> None:
        assert sandbox.evaluate("len(value)", [1, 2, 3]) == 3

    def test_builtin_round(self, sandbox: ExpressionSandbox) -> None:
        assert sandbox.evaluate("round(value, 2)", 3.14159) == 3.14

    def test_conditional(self, sandbox: ExpressionSandbox) -> None:
        assert sandbox.evaluate("'yes' if value > 0 else 'no'", 5) == "yes"


class TestBlockedExpressions:
    def test_blocked_import(self, sandbox: ExpressionSandbox) -> None:
        with pytest.raises(UnsafeExpressionError, match="__import__"):
            sandbox.evaluate("__import__('os')", "x")

    def test_blocked_exec(self, sandbox: ExpressionSandbox) -> None:
        with pytest.raises(UnsafeExpressionError, match="exec"):
            sandbox.evaluate("exec('pass')", "x")

    def test_blocked_eval(self, sandbox: ExpressionSandbox) -> None:
        with pytest.raises(UnsafeExpressionError, match="eval"):
            sandbox.evaluate("eval('1+1')", "x")

    def test_blocked_open(self, sandbox: ExpressionSandbox) -> None:
        with pytest.raises(UnsafeExpressionError, match="open"):
            sandbox.evaluate("open('/etc/passwd')", "x")

    def test_blocked_dunder_access(self, sandbox: ExpressionSandbox) -> None:
        with pytest.raises(UnsafeExpressionError, match="dunder"):
            sandbox.evaluate("value.__class__", "x")

    def test_blocked_globals(self, sandbox: ExpressionSandbox) -> None:
        with pytest.raises(UnsafeExpressionError, match="globals"):
            sandbox.evaluate("globals()", "x")

    def test_blocked_compile(self, sandbox: ExpressionSandbox) -> None:
        with pytest.raises(UnsafeExpressionError, match="compile"):
            sandbox.evaluate("compile('x', '', 'exec')", "x")
