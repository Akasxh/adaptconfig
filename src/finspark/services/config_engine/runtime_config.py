"""Helpers for reading and updating persisted runtime configuration JSON."""

from __future__ import annotations

import json
from typing import Any

from finspark.core.json_utils import safe_json_loads


def load_runtime_config(config: Any) -> dict[str, Any]:
    """Return full_config with editable sidecar columns overlaid.

    ``configurations.full_config`` is what validation, simulation, and export
    consume, while PATCH updates editable columns like ``field_mappings``.
    Overlaying here keeps those runtime paths aligned even for rows written
    before the PATCH sync fix.
    """
    full_config = safe_json_loads(getattr(config, "full_config", None), {})
    if not isinstance(full_config, dict):
        full_config = {}

    for section in ("field_mappings", "transformation_rules", "hooks"):
        value = safe_json_loads(getattr(config, section, None), None)
        if value is not None:
            full_config[section] = value

    return full_config


def persist_runtime_section(config: Any, section: str, value: Any) -> None:
    """Update one editable section in both its column and full_config."""
    setattr(config, section, json.dumps(value))
    full_config = load_runtime_config(config)
    full_config[section] = value
    config.full_config = json.dumps(full_config)
