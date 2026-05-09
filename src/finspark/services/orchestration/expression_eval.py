"""Safe expression evaluator for workflow condition nodes.

Supports JSONPath-like context access ($.context.x.y.z),
comparison operators (==, !=, >=, <=, >, <), boolean
operators (AND, OR), and string/number/boolean literals.
Does NOT use eval().
"""

from __future__ import annotations

import ast
import re
from typing import Any


# Matches a $.context.path.to.value token
_PATH_RE = re.compile(r"\$\.context(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+")

# Comparison operators ordered longest-first to avoid partial matches
_COMPARISON_OPS = (">=", "<=", "!=", "==", ">", "<")


class ExpressionEvaluator:
    """Evaluate condition expressions against workflow context."""

    def evaluate(self, expression: str, context: dict[str, Any]) -> bool:
        """Evaluate a boolean expression against workflow context.

        Supports AND/OR with simple comparison clauses.
        """
        expression = expression.strip()
        if not expression:
            return True

        # Split on OR first (lower precedence), then AND
        return self._evaluate_or(expression, context)

    def resolve_path(self, path: str, context: dict[str, Any]) -> Any:
        """Resolve $.context.x.y.z to a value in context dict."""
        if not path.startswith("$.context."):
            return None

        keys = path[len("$.context."):].split(".")
        current: Any = context
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None
        return current

    # -- internal helpers --

    def _evaluate_or(self, expr: str, ctx: dict[str, Any]) -> bool:
        """Split on OR, evaluate each AND-group."""
        parts = self._split_boolean(expr, " OR ")
        return any(self._evaluate_and(part, ctx) for part in parts)

    def _evaluate_and(self, expr: str, ctx: dict[str, Any]) -> bool:
        """Split on AND, evaluate each comparison."""
        parts = self._split_boolean(expr, " AND ")
        return all(self._evaluate_comparison(part, ctx) for part in parts)

    def _split_boolean(self, expr: str, sep: str) -> list[str]:
        """Split expression by a boolean operator, respecting quoted strings."""
        parts: list[str] = []
        current: list[str] = []
        in_quote: str | None = None

        i = 0
        while i < len(expr):
            ch = expr[i]
            if ch in ("'", '"') and not in_quote:
                in_quote = ch
            elif ch == in_quote:
                in_quote = None

            if in_quote is None and expr[i:].startswith(sep):
                parts.append("".join(current).strip())
                current = []
                i += len(sep)
                continue

            current.append(ch)
            i += 1

        remaining = "".join(current).strip()
        if remaining:
            parts.append(remaining)

        return parts

    def _evaluate_comparison(
        self, expr: str, ctx: dict[str, Any],
    ) -> bool:
        """Evaluate a single comparison like '$.context.x >= 5'."""
        expr = expr.strip()

        for op in _COMPARISON_OPS:
            idx = self._find_operator(expr, op)
            if idx < 0:
                continue

            left_raw = expr[:idx].strip()
            right_raw = expr[idx + len(op):].strip()

            left = self._resolve_value(left_raw, ctx)
            right = self._resolve_value(right_raw, ctx)

            return self._compare(left, right, op)

        # No operator found -- treat entire expression as a truthy check
        val = self._resolve_value(expr, ctx)
        return bool(val)

    def _find_operator(self, expr: str, op: str) -> int:
        """Find operator position outside of quoted strings."""
        in_quote: str | None = None
        for i, ch in enumerate(expr):
            if ch in ("'", '"') and not in_quote:
                in_quote = ch
            elif ch == in_quote:
                in_quote = None
            if in_quote is None and expr[i:i + len(op)] == op:
                # Avoid matching >= when looking for >
                if op == ">" and i + 1 < len(expr) and expr[i + 1] == "=":
                    continue
                if op == "<" and i + 1 < len(expr) and expr[i + 1] == "=":
                    continue
                # Avoid matching != when looking for =
                if op == "==" and i > 0 and expr[i - 1] in ("!", ">", "<"):
                    continue
                return i
        return -1

    def _resolve_value(self, raw: str, ctx: dict[str, Any]) -> Any:
        """Resolve a token to a Python value."""
        raw = raw.strip()

        # JSONPath reference
        if raw.startswith("$.context."):
            return self.resolve_path(raw, ctx)

        # Boolean literals
        if raw.lower() == "true":
            return True
        if raw.lower() == "false":
            return False
        if raw.lower() == "null" or raw.lower() == "none":
            return None

        # Quoted string
        if (raw.startswith("'") and raw.endswith("'")) or (
            raw.startswith('"') and raw.endswith('"')
        ):
            return raw[1:-1]

        # Number
        try:
            return ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            pass

        # Fall back to string
        return raw

    @staticmethod
    def _compare(left: Any, right: Any, op: str) -> bool:
        """Perform a comparison. Returns False on type mismatch."""
        if left is None or right is None:
            if op == "==":
                return left is None and right is None
            if op == "!=":
                return not (left is None and right is None)
            return False

        try:
            if op == "==":
                return left == right
            if op == "!=":
                return left != right
            if op == ">=":
                return left >= right
            if op == "<=":
                return left <= right
            if op == ">":
                return left > right
            if op == "<":
                return left < right
        except TypeError:
            return False

        return False
