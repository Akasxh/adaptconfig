"""Token-level parser + closed-registry evaluator for the transformation DSL.

Grammar
-------

::

    expr  := step ('|' step)*
    step  := IDENT '(' args? ')'
    args  := arg (',' arg)*
    arg   := IDENT  -- e.g., the ``x`` placeholder for the threaded value
           | NUMBER -- 123, 1_000_000, 1.5, -2
           | STRING -- "..." with \\\\ and \\" escapes only

Semantics
---------

- The expression operates on a single threaded value. Each step receives the
  output of the previous step (or the original input for the first step) as
  its first argument; literal arguments to the function follow.
- The bare identifier ``x`` is a placeholder that resolves to the currently
  threaded value. Any other identifier is rejected.
- Function names are looked up in :data:`_REGISTRY`. There is no other way
  to dispatch — no ``eval``, no ``getattr``, no module import.

This file deliberately avoids:

- ``eval`` / ``exec`` / ``compile``
- ``__import__``
- ``getattr`` / ``setattr`` / ``delattr`` on user-controlled names
- f-string interpolation of user input into other source code
- ``subprocess`` / ``os.system`` / similar IPC sinks
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


class TransformationError(ValueError):
    """Raised when an expression cannot be parsed or evaluated."""


# ---------------------------------------------------------------------------
# Registry of safe callables
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> int:
    """Coerce a value to int, tolerating thousands separators in strings."""
    if isinstance(value, bool):
        # bool is a subclass of int — preserve numeric-but-not-bool semantics.
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("_", "")
        if not cleaned:
            raise TransformationError("int(x): empty string")
        try:
            if "." in cleaned:
                return int(float(cleaned))
            return int(cleaned)
        except ValueError as exc:
            raise TransformationError(f"int(x): cannot parse {value!r}") from exc
    raise TransformationError(f"int(x): unsupported type {type(value).__name__}")


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("_", "")
        if not cleaned:
            raise TransformationError("float(x): empty string")
        try:
            return float(cleaned)
        except ValueError as exc:
            raise TransformationError(f"float(x): cannot parse {value!r}") from exc
    raise TransformationError(f"float(x): unsupported type {type(value).__name__}")


def _strip_chars(value: Any, chars: str) -> str:
    if not isinstance(value, str):
        value = str(value)
    if not isinstance(chars, str):
        raise TransformationError("strip(s): argument must be a string literal")
    return value.strip(chars)


def _upper(value: Any) -> str:
    return str(value).upper()


def _lower(value: Any) -> str:
    return str(value).lower()


def _clamp(value: Any, lo: float | int, hi: float | int) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        # Coerce strings like "2,000" the same way int(x) does so chained
        # expressions like 'int(x) | clamp(0, 100)' work as documented.
        try:
            value = _coerce_float(value)
        except TransformationError as exc:
            raise TransformationError(
                f"clamp(lo, hi): non-numeric input {value!r}"
            ) from exc
    if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
        raise TransformationError("clamp(lo, hi): bounds must be numeric literals")
    if lo > hi:
        raise TransformationError("clamp(lo, hi): lo must be <= hi")
    return min(max(value, lo), hi)


# Map of human-readable date format tokens -> Python strptime/strftime tokens.
# Order matters: longest tokens first to avoid prefix collisions.
_DATE_TOKEN_MAP: list[tuple[str, str]] = [
    ("YYYY", "%Y"),
    ("YY", "%y"),
    ("MM", "%m"),
    ("DD", "%d"),
    ("HH", "%H"),
    ("mm", "%M"),
    ("SS", "%S"),
    ("ss", "%S"),
]


def _translate_date_format(fmt: str) -> str:
    """Translate a user-friendly date format to Python's strftime grammar."""
    out: list[str] = []
    i = 0
    while i < len(fmt):
        matched = False
        for human, code in _DATE_TOKEN_MAP:
            if fmt[i : i + len(human)] == human:
                out.append(code)
                i += len(human)
                matched = True
                break
        if not matched:
            out.append(fmt[i])
            i += 1
    return "".join(out)


def _parse_date(value: Any, fmt: str) -> str:
    if not isinstance(fmt, str):
        raise TransformationError("parse_date(fmt): format must be a string literal")
    if not isinstance(value, str):
        value = str(value)
    py_fmt = _translate_date_format(fmt)
    try:
        dt = datetime.strptime(value.strip(), py_fmt)
    except ValueError as exc:
        raise TransformationError(
            f"parse_date(fmt): cannot parse {value!r} with format {fmt!r}"
        ) from exc
    return dt.date().isoformat()


# A registry entry: (callable, min_literal_args, max_literal_args)
# `literal_args` are arguments BEYOND the threaded value (i.e., the literals
# the user types in parens, with any ``x`` placeholders already discarded).
_REGISTRY: dict[str, tuple[Callable[..., Any], int, int]] = {
    "int": (_coerce_int, 0, 0),
    "float": (_coerce_float, 0, 0),
    "upper": (_upper, 0, 0),
    "lower": (_lower, 0, 0),
    "strip": (_strip_chars, 1, 1),
    "parse_date": (_parse_date, 1, 1),
    "clamp": (_clamp, 2, 2),
}


# Mapping for the legacy enum transformations (strings stored on
# FieldMapping.transformation). When ``transformation_expr`` is blank, the
# simulator falls through to this table for backwards compatibility.
_ENUM_TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "upper": _upper,
    "uppercase": _upper,
    "lower": _lower,
    "lowercase": _lower,
    "to_string": lambda v: "" if v is None else str(v),
    "parse_number": _coerce_float,
    "parse_boolean": lambda v: str(v).strip().lower() in {"true", "1", "yes", "y"},
    "validate_email": lambda v: str(v).strip().lower(),
    "normalize_phone": lambda v: re.sub(r"\D", "", str(v)),
    "parse_date": lambda v: _parse_date(v, "YYYY-MM-DD"),
    "format_date": lambda v: _parse_date(v, "YYYY-MM-DD"),
}


def apply_enum_transformation(value: Any, transformation: str | None) -> Any:
    """Apply a legacy enum transformation. Unknown enums return the value as-is."""
    if not transformation:
        return value
    fn = _ENUM_TRANSFORMS.get(transformation)
    if fn is None:
        return value
    try:
        return fn(value)
    except TransformationError:
        # Even legacy enums can fail (e.g. parse_date on garbage); never crash
        # the caller — the simulator is best-effort.
        return value
    except (ValueError, TypeError):
        return value


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# A bounded set of token kinds. We tokenize by scanning a single regex with
# named groups; anything that doesn't match is a syntax error.
_TOKEN_RE = re.compile(
    r"""
      (?P<WS>\s+)
    | (?P<STRING>"(?:\\.|[^"\\])*")
    | (?P<FLOAT>-?\d+(?:_\d+)*\.\d+(?:_\d+)*)
    | (?P<INT>-?\d+(?:_\d+)*)
    | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<LPAREN>\()
    | (?P<RPAREN>\))
    | (?P<COMMA>,)
    | (?P<PIPE>\|)
    """,
    re.VERBOSE,
)


# Maximum input expression length. Bounds parser work for adversarial inputs.
_MAX_EXPR_LEN = 256


def _tokenize(expr: str) -> list[tuple[str, str, int]]:
    """Return a list of ``(kind, lexeme, position)`` tokens, omitting whitespace."""
    if len(expr) > _MAX_EXPR_LEN:
        raise TransformationError(
            f"expression too long ({len(expr)} chars, max {_MAX_EXPR_LEN})"
        )
    tokens: list[tuple[str, str, int]] = []
    pos = 0
    while pos < len(expr):
        m = _TOKEN_RE.match(expr, pos)
        if not m:
            raise TransformationError(
                f"unexpected character {expr[pos]!r} at position {pos}"
            )
        kind = m.lastgroup or ""
        lexeme = m.group()
        if kind != "WS":
            tokens.append((kind, lexeme, pos))
        pos = m.end()
    return tokens


def _decode_string_literal(lexeme: str) -> str:
    """Decode a quoted string lexeme, supporting only ``\\"`` and ``\\\\`` escapes."""
    body = lexeme[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in ('"', "\\"):
                out.append(nxt)
                i += 2
                continue
            raise TransformationError(
                f"unsupported escape sequence \\{nxt} in string literal"
            )
        out.append(c)
        i += 1
    return "".join(out)


def _decode_number(kind: str, lexeme: str) -> int | float:
    cleaned = lexeme.replace("_", "")
    if kind == "INT":
        return int(cleaned)
    return float(cleaned)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


# A parsed step: (function_name, [literal_args], position)
ParsedStep = tuple[str, list[Any], int]


def _parse(expr: str) -> list[ParsedStep]:
    """Parse a full expression string into a list of steps."""
    if not expr or not expr.strip():
        raise TransformationError("empty expression")
    tokens = _tokenize(expr)
    if not tokens:
        raise TransformationError("empty expression")

    steps: list[ParsedStep] = []
    i = 0
    while i < len(tokens):
        step, i = _parse_step(tokens, i)
        steps.append(step)
        if i < len(tokens):
            kind, _, pos = tokens[i]
            if kind != "PIPE":
                raise TransformationError(
                    f"expected '|' between steps at position {pos}"
                )
            i += 1
            if i >= len(tokens):
                raise TransformationError("trailing '|' with no step after")
    return steps


def _parse_step(
    tokens: list[tuple[str, str, int]], i: int
) -> tuple[ParsedStep, int]:
    kind, lexeme, pos = tokens[i]
    if kind != "IDENT":
        raise TransformationError(
            f"expected function name at position {pos}, got {lexeme!r}"
        )
    name = lexeme
    if name not in _REGISTRY:
        raise TransformationError(
            f"unknown function {name!r} at position {pos} "
            f"(allowed: {', '.join(sorted(_REGISTRY))})"
        )
    i += 1
    if i >= len(tokens) or tokens[i][0] != "LPAREN":
        raise TransformationError(
            f"expected '(' after function name {name!r} at position {pos}"
        )
    i += 1

    literal_args: list[Any] = []
    placeholder_count = 0

    # Empty arg list: `name()`
    if i < len(tokens) and tokens[i][0] == "RPAREN":
        i += 1
        _validate_arity(name, literal_args, placeholder_count, pos)
        return (name, literal_args, pos), i

    while i < len(tokens):
        arg_kind, arg_lex, arg_pos = tokens[i]
        if arg_kind == "STRING":
            literal_args.append(_decode_string_literal(arg_lex))
        elif arg_kind in ("INT", "FLOAT"):
            literal_args.append(_decode_number(arg_kind, arg_lex))
        elif arg_kind == "IDENT":
            if arg_lex != "x":
                raise TransformationError(
                    f"unsupported identifier {arg_lex!r} at position {arg_pos} "
                    f"(only the placeholder 'x' is allowed in argument lists)"
                )
            placeholder_count += 1
            if placeholder_count > 1:
                raise TransformationError(
                    f"{name}(...) at position {pos}: 'x' placeholder may "
                    f"appear at most once per call"
                )
        else:
            raise TransformationError(
                f"unexpected token {arg_lex!r} at position {arg_pos}"
            )
        i += 1
        if i >= len(tokens):
            raise TransformationError(
                f"missing ')' for call to {name!r} at position {pos}"
            )
        sep_kind, sep_lex, sep_pos = tokens[i]
        if sep_kind == "RPAREN":
            i += 1
            _validate_arity(name, literal_args, placeholder_count, pos)
            return (name, literal_args, pos), i
        if sep_kind != "COMMA":
            raise TransformationError(
                f"expected ',' or ')' at position {sep_pos}, got {sep_lex!r}"
            )
        i += 1
        if i >= len(tokens):
            raise TransformationError(
                f"trailing ',' in arg list for {name!r}"
            )

    raise TransformationError(f"unterminated call to {name!r} at position {pos}")


def _validate_arity(
    name: str, literal_args: list[Any], placeholder_count: int, pos: int
) -> None:
    _, lo, hi = _REGISTRY[name]
    if not (lo <= len(literal_args) <= hi):
        if lo == hi:
            expected = f"exactly {lo}"
        else:
            expected = f"between {lo} and {hi}"
        raise TransformationError(
            f"{name}(...) at position {pos} expects {expected} literal "
            f"argument(s), got {len(literal_args)}"
        )
    if placeholder_count > 1:
        raise TransformationError(
            f"{name}(...) at position {pos}: 'x' placeholder may appear at most once"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_transformation(value: Any, expr: str) -> Any:
    """Parse ``expr`` and apply it to ``value``. Raises :class:`TransformationError`."""
    steps = _parse(expr)
    current = value
    for name, literal_args, _pos in steps:
        fn, _, _ = _REGISTRY[name]
        try:
            current = fn(current, *literal_args)
        except TransformationError:
            raise
        except (ValueError, TypeError) as exc:
            raise TransformationError(f"{name}(...) failed: {exc}") from exc
    return current


def apply_transformation_safe(
    value: Any,
    expr: str | None,
    fallback_transformation: str | None = None,
) -> Any:
    """Apply ``expr`` defensively; on any failure fall back to the enum.

    This is the runtime helper the simulator uses. It guarantees the caller
    never sees a ``TransformationError`` so a typo in a user-supplied
    expression cannot crash the simulation pipeline.
    """
    if expr and expr.strip():
        try:
            return apply_transformation(value, expr)
        except TransformationError as exc:
            logger.debug(
                "apply_transformation_safe fallback expr=%r error=%s", expr, exc
            )
            # Fall through to enum-based transformation.
    return apply_enum_transformation(value, fallback_transformation)


def validate_expression(expr: str | None) -> tuple[bool, str | None]:
    """Return ``(is_valid, error_message)`` for an optional expression.

    A blank/None expression is considered valid (nothing to do).
    """
    if expr is None or not expr.strip():
        return True, None
    try:
        _parse(expr)
    except TransformationError as exc:
        return False, str(exc)
    return True, None
