"""Per-field runtime transformation engine.

Tiny safe DSL evaluated against a closed allow-list of pure callables.
Public surface:

- ``apply_transformation(value, expr)``: parse and evaluate an expression,
  raising :class:`TransformationError` on any failure. Used in tests and by
  callers that want strict error propagation.
- ``apply_transformation_safe(value, expr, fallback_transformation=None)``:
  thin wrapper that never raises. On any parser/eval failure, it falls back
  to the enum ``fallback_transformation`` (today's behaviour) so simulator
  paths can never crash on a bad user-supplied expression.
- ``validate_expression(expr)``: returns ``(is_valid, error_message)`` for
  use by API surfaces that want to surface inline validation feedback.

There is **no** ``eval``, ``exec``, ``compile``, ``__import__``, ``getattr``,
or ``subprocess`` anywhere in this module. Function dispatch is done through
an explicit registry of pure-Python callables.
"""

from finspark.services.transformation.engine import (
    TransformationError,
    apply_enum_transformation,
    apply_transformation,
    apply_transformation_safe,
    validate_expression,
)

__all__ = [
    "TransformationError",
    "apply_enum_transformation",
    "apply_transformation",
    "apply_transformation_safe",
    "validate_expression",
]
