"""Exceptions raised by the chain runtime."""
from __future__ import annotations


class ChainError(Exception):
    """Base class for chain-runtime errors."""


class ChainCycleError(ChainError):
    """Raised when ``depends_on`` forms a cycle, references an unknown id, or
    when two endpoints declare the same ``id``.

    The HTTP layer converts this into a 400 response so callers see a clear
    "your chain is invalid" message instead of a 500.
    """
