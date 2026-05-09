"""Safe expression evaluator for custom transformations."""

import ast
from typing import Any

_ALLOWED_BUILTINS = {
    "len", "str", "int", "float", "bool", "list", "dict",
    "min", "max", "abs", "round", "sorted", "reversed",
    "enumerate", "zip", "map", "filter", "range",
    "type", "isinstance", "hasattr", "getattr",
}

_BLOCKED_NAMES = {
    "import", "exec", "eval", "compile", "__import__",
    "open", "globals", "locals", "vars", "dir",
    "breakpoint", "exit", "quit", "input", "print",
}


class UnsafeExpressionError(Exception):
    """Raised when an expression contains blocked constructs."""


class ExpressionSandbox:
    """Evaluates Python expressions in a restricted environment."""

    def evaluate(
        self,
        expression: str,
        value: Any,
        context: dict[str, Any] | None = None,
    ) -> Any:
        tree = ast.parse(expression, mode="eval")
        self._validate_ast(tree)

        safe_builtins = {
            name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
            for name in _ALLOWED_BUILTINS
        }

        namespace: dict[str, Any] = {
            "__builtins__": safe_builtins,
            "value": value,
        }
        if context is not None:
            namespace["context"] = context

        return eval(compile(tree, "<sandbox>", "eval"), namespace)  # noqa: S307

    def _validate_ast(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise UnsafeExpressionError("Import statements are not allowed")
            if isinstance(node, ast.Call):
                self._check_call(node)
            if isinstance(node, ast.Attribute):
                self._check_attribute(node)
            if isinstance(node, ast.Name) and node.id in _BLOCKED_NAMES:
                raise UnsafeExpressionError(f"Access to '{node.id}' is not allowed")

    def _check_call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in _BLOCKED_NAMES:
            raise UnsafeExpressionError(
                f"Call to '{node.func.id}' is not allowed"
            )

    def _check_attribute(self, node: ast.Attribute) -> None:
        if node.attr.startswith("__") and node.attr.endswith("__"):
            raise UnsafeExpressionError(
                f"Access to dunder attribute '{node.attr}' is not allowed"
            )
