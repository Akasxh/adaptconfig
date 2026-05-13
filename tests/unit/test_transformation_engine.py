"""Unit tests for the per-field transformation DSL engine.

Covers:

- The persona acceptance case ``int(x) | clamp(0, 1_000_000)`` over ``"2,000"``.
- The full closed allow-list of safe callables.
- Rejection of every dangerous identifier the persona named (eval, exec,
  compile, __import__, getattr, subprocess, lambda, ...).
- Defensive parsing edge cases: empty input, oversized input, malformed
  punctuation, unsupported escape sequences, mismatched parentheses.
- Fallback semantics of :func:`apply_transformation_safe` (never raises;
  drops to the legacy enum when the new expr is bad).
"""

from __future__ import annotations

import pytest

from finspark.services.transformation import (
    TransformationError,
    apply_enum_transformation,
    apply_transformation,
    apply_transformation_safe,
    validate_expression,
)

# ---------------------------------------------------------------------------
# Persona acceptance test
# ---------------------------------------------------------------------------


def test_persona_acceptance_int_then_clamp() -> None:
    """`int(x) | clamp(0, 1_000_000)` applied to "2,000" returns 2000."""
    assert apply_transformation("2,000", "int(x) | clamp(0, 1_000_000)") == 2000


# ---------------------------------------------------------------------------
# Single-step transforms
# ---------------------------------------------------------------------------


class TestSingleStepTransforms:
    def test_int_strips_commas(self) -> None:
        assert apply_transformation("2,000", "int(x)") == 2000

    def test_int_strips_underscores(self) -> None:
        assert apply_transformation("1_000_000", "int(x)") == 1_000_000

    def test_int_from_int(self) -> None:
        assert apply_transformation(42, "int(x)") == 42

    def test_int_from_float_string_truncates(self) -> None:
        assert apply_transformation("2.7", "int(x)") == 2

    def test_int_rejects_garbage(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("abc", "int(x)")

    def test_float_basic(self) -> None:
        assert apply_transformation("1.5", "float(x)") == 1.5

    def test_float_with_thousands_separator(self) -> None:
        assert apply_transformation("1,234.5", "float(x)") == 1234.5

    def test_upper(self) -> None:
        assert apply_transformation("abc", "upper(x)") == "ABC"

    def test_lower(self) -> None:
        assert apply_transformation("ABC", "lower(x)") == "abc"

    def test_strip_dollar_sign(self) -> None:
        assert apply_transformation("$100$", 'strip("$")') == "100"

    def test_strip_multiple_chars(self) -> None:
        assert apply_transformation("---hello---", 'strip("-")') == "hello"

    def test_strip_with_escaped_quote_in_arg(self) -> None:
        assert apply_transformation('"quoted"', 'strip("\\"")') == "quoted"

    def test_clamp_above_max(self) -> None:
        assert apply_transformation(200, "clamp(0, 100)") == 100

    def test_clamp_below_min(self) -> None:
        assert apply_transformation(-5, "clamp(0, 100)") == 0

    def test_clamp_within_range(self) -> None:
        assert apply_transformation(50, "clamp(0, 100)") == 50

    def test_clamp_with_string_input(self) -> None:
        # clamp coerces string thousands-separated input via its internal
        # float coercion so chains starting from string mock data still work.
        assert apply_transformation("50", "clamp(0, 100)") == 50.0

    def test_clamp_rejects_non_numeric(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("not-a-number", "clamp(0, 100)")

    def test_clamp_lo_must_be_le_hi(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation(50, "clamp(100, 0)")

    def test_parse_date_indian_format(self) -> None:
        assert apply_transformation("26/03/2024", 'parse_date("DD/MM/YYYY")') == "2024-03-26"

    def test_parse_date_iso_format_round_trip(self) -> None:
        assert apply_transformation("2024-03-26", 'parse_date("YYYY-MM-DD")') == "2024-03-26"

    def test_parse_date_us_format(self) -> None:
        assert apply_transformation("03/26/2024", 'parse_date("MM/DD/YYYY")') == "2024-03-26"

    def test_parse_date_two_digit_year(self) -> None:
        assert apply_transformation("26/03/24", 'parse_date("DD/MM/YY")') == "2024-03-26"

    def test_parse_date_rejects_bad_input(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("not-a-date", 'parse_date("DD/MM/YYYY")')


# ---------------------------------------------------------------------------
# Chained pipelines
# ---------------------------------------------------------------------------


class TestChainedPipelines:
    def test_strip_then_int(self) -> None:
        assert apply_transformation("$100", 'strip("$") | int(x)') == 100

    def test_lower_then_strip_padding(self) -> None:
        assert apply_transformation("  HELLO  ", 'strip(" ") | lower(x)') == "hello"

    def test_int_then_clamp_then_passes_int_through(self) -> None:
        assert apply_transformation("12,000", "int(x) | clamp(0, 10_000)") == 10_000

    def test_three_step_chain(self) -> None:
        # strip currency symbol, parse to int, clamp to allowed band
        assert (
            apply_transformation("₹50,000", 'strip("₹") | int(x) | clamp(0, 1_000_000)')
            == 50_000
        )

    def test_whitespace_tolerance_around_pipes(self) -> None:
        assert apply_transformation("abc", "  upper(x)  |  lower(x)  ") == "abc"

    def test_whitespace_tolerance_inside_args(self) -> None:
        assert apply_transformation("2,000", "int( x ) | clamp( 0 , 100_000 )") == 2000


# ---------------------------------------------------------------------------
# Rejection of dangerous identifiers
# ---------------------------------------------------------------------------


DANGEROUS_EXPRS = [
    # The persona's exact named threats:
    "eval(x)",
    'eval("import os")',
    "exec(x)",
    "compile(x)",
    '__import__("os")',
    "getattr(x)",
    "setattr(x)",
    'subprocess(x, "ls")',
    "open(x)",
    "os(x)",
    "sys(x)",
    # f-string-style interpolation attempts:
    'f"{x}"',
    # dunder access on the threaded value:
    "x.__class__",
    "x.system",
    # Lambda / comprehension attempts:
    "lambda x: x",
    "[x for y in z]",
    # Bare attribute / index access:
    "x[0]",
    "x.upper",
    # Operator misuse:
    "x + 1",
    "1 + 1",
    # Random unknown identifier:
    "danger(x)",
    "foo(x)",
]


@pytest.mark.parametrize("expr", DANGEROUS_EXPRS)
def test_rejects_dangerous_expression(expr: str) -> None:
    valid, error = validate_expression(expr)
    assert not valid, f"Expression should have been rejected: {expr!r}"
    assert error
    with pytest.raises(TransformationError):
        apply_transformation("anything", expr)


def test_rejects_unknown_identifier_inside_arg_list() -> None:
    # `y` is not the placeholder `x` and not a literal — must be rejected.
    with pytest.raises(TransformationError):
        apply_transformation("foo", "upper(y)")


def test_rejects_attribute_access_in_arg() -> None:
    with pytest.raises(TransformationError):
        apply_transformation("foo", "upper(x.upper)")


# ---------------------------------------------------------------------------
# Syntax & arity edge cases
# ---------------------------------------------------------------------------


class TestSyntaxEdgeCases:
    def test_empty_string_rejected(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("foo", "")

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("foo", "   ")

    def test_unterminated_call(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("foo", "int(")

    def test_trailing_pipe(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("foo", "int(x) |")

    def test_leading_pipe(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("foo", "| int(x)")

    def test_missing_pipe_between_steps(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("foo", "int(x) clamp(0, 1)")

    def test_too_many_args(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("foo", "upper(x, x)")

    def test_too_few_args(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("$100", "strip()")

    def test_clamp_needs_two_literals(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation(50, "clamp(0)")

    def test_clamp_rejects_non_literal_args(self) -> None:
        # `x` is NOT a literal — clamp's bounds must be numeric literals.
        with pytest.raises(TransformationError):
            apply_transformation(50, "clamp(x, 100)")

    def test_string_with_unsupported_escape(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("foo", 'strip("\\n")')

    def test_string_with_supported_escape(self) -> None:
        assert apply_transformation('"hello"', 'strip("\\"")') == "hello"

    def test_oversized_expression_rejected(self) -> None:
        long_expr = "upper(x)" + " | upper(x)" * 100
        with pytest.raises(TransformationError):
            apply_transformation("foo", long_expr)

    def test_unbalanced_parens(self) -> None:
        with pytest.raises(TransformationError):
            apply_transformation("foo", "upper(x))")


# ---------------------------------------------------------------------------
# validate_expression
# ---------------------------------------------------------------------------


class TestValidateExpression:
    def test_valid_returns_none_error(self) -> None:
        valid, error = validate_expression("int(x) | clamp(0, 100)")
        assert valid is True
        assert error is None

    def test_blank_is_valid(self) -> None:
        assert validate_expression(None) == (True, None)
        assert validate_expression("") == (True, None)
        assert validate_expression("   ") == (True, None)

    def test_invalid_returns_message(self) -> None:
        valid, error = validate_expression("eval(x)")
        assert valid is False
        assert error is not None
        assert "eval" in error


# ---------------------------------------------------------------------------
# apply_transformation_safe / apply_enum_transformation
# ---------------------------------------------------------------------------


class TestSafeFallback:
    def test_safe_returns_value_when_no_expr_no_enum(self) -> None:
        assert apply_transformation_safe("hello", None, None) == "hello"
        assert apply_transformation_safe("hello", "", None) == "hello"

    def test_safe_uses_expr_when_valid(self) -> None:
        assert apply_transformation_safe("abc", "upper(x)", "lower") == "ABC"

    def test_safe_falls_back_to_enum_on_invalid_expr(self) -> None:
        assert apply_transformation_safe("abc", "eval(x)", "upper") == "ABC"

    def test_safe_falls_back_to_value_when_enum_missing(self) -> None:
        # No enum and a bad expr => return the original value, not raise.
        assert apply_transformation_safe("hello", "eval(x)", None) == "hello"

    def test_safe_never_raises_on_runtime_error(self) -> None:
        # A syntactically valid expression that fails at runtime (int on
        # garbage) must drop to the enum, not propagate an exception.
        assert apply_transformation_safe("not-a-number", "int(x)", "upper") == "NOT-A-NUMBER"

    def test_enum_passthrough_on_unknown(self) -> None:
        # Unknown enum returns value unchanged (legacy behaviour preserved).
        assert apply_enum_transformation("abc", "definitely-not-real") == "abc"

    def test_enum_passthrough_on_blank(self) -> None:
        assert apply_enum_transformation("abc", None) == "abc"
        assert apply_enum_transformation("abc", "") == "abc"

    def test_enum_normalize_phone(self) -> None:
        assert apply_enum_transformation("+91-987-654-3210", "normalize_phone") == "919876543210"

    def test_enum_parse_boolean_truthy(self) -> None:
        assert apply_enum_transformation("YES", "parse_boolean") is True
        assert apply_enum_transformation("true", "parse_boolean") is True
        assert apply_enum_transformation("1", "parse_boolean") is True

    def test_enum_parse_boolean_falsy(self) -> None:
        assert apply_enum_transformation("no", "parse_boolean") is False
        assert apply_enum_transformation("0", "parse_boolean") is False
        assert apply_enum_transformation("", "parse_boolean") is False


# ---------------------------------------------------------------------------
# Simulator integration sanity (lightweight — full coverage in integration)
# ---------------------------------------------------------------------------


def test_simulator_request_payload_applies_expr() -> None:
    """`_build_sample_request` threads each value through its mapping's expr."""
    from finspark.services.simulation.simulator import IntegrationSimulator

    config = {
        "field_mappings": [
            {
                "source_field": "loan_amount",
                "target_field": "amount",
                "transformation_expr": "int(x) | clamp(0, 100_000)",
            },
            {
                "source_field": "pan_number",
                "target_field": "pan",
                "transformation_expr": "upper(x)",
            },
            {
                # Invalid expr: simulator must NOT crash; falls back to enum.
                "source_field": "email_address",
                "target_field": "email",
                "transformation_expr": "eval(x)",
                "transformation": "lower",
            },
        ]
    }
    payload = IntegrationSimulator._build_sample_request(config)
    # loan_amount mock is 500000.00 -> int -> 500000 -> clamp(0, 100_000) -> 100000
    assert payload["loan_amount"] == 100_000
    # pan_number mock is "ABCDE1234F" — already upper, stays upper.
    assert payload["pan_number"] == "ABCDE1234F"
    # email — bad expr falls back to enum 'lower'; mock data is already lower.
    assert payload["email_address"] == "rajesh.kumar@example.com"
