"""Safe JSON parsing utilities for database-stored values."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def safe_json_loads(value: str | None, default: Any = None) -> Any:
    """Parse a JSON string, returning *default* on None/empty/malformed input."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Malformed JSON: %s...", value[:100])
        return default
