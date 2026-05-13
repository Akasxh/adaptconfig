"""Tiny JSONPath-ish resolver for the chain runtime.

The MVP only needs to resolve simple paths emitted by an LLM-generated config:
``$.access_token``, ``$.data.items[0].id``, ``$.user.name``. We deliberately
do NOT pull in :pypi:`jsonpath-ng` -- that adds a runtime dependency and a
parser surface area we don't need.

Supported syntax:

* leading ``$`` (optional) and ``$.`` are stripped.
* ``.`` separates dict keys.
* ``[N]`` indexes a list (e.g. ``items[0]``, ``data.users[2].id``).
* missing keys / out-of-range indices return ``None`` instead of raising.

Anything more exotic (filters, wildcards, recursive descent) is out of scope
for the MVP and would belong in a follow-up that swaps in :pypi:`jsonpath-ng`.
"""
from __future__ import annotations

import re
from typing import Any

# Matches a single segment such as ``foo`` or ``foo[0]`` or ``foo[12]``.
_SEGMENT_RE = re.compile(r"^([^.\[\]]+)((?:\[\d+\])*)$")
_INDEX_RE = re.compile(r"\[(\d+)\]")


def _split_segments(path: str) -> list[str]:
    """Split a normalised path like ``a.b[0].c`` into its dot segments."""
    cleaned = path.strip()
    if cleaned.startswith("$."):
        cleaned = cleaned[2:]
    elif cleaned == "$":
        return []
    elif cleaned.startswith("$"):
        cleaned = cleaned[1:]
    if not cleaned:
        return []
    return cleaned.split(".")


def extract_path(data: Any, path: str) -> Any:
    """Resolve ``path`` against ``data``. Returns ``None`` if any segment
    is missing or the structure does not match.

    Empty / None path returns ``data`` itself, which makes "extract the entire
    response" the trivial default.
    """
    if path is None:
        return data
    if not isinstance(path, str):
        return None
    if path.strip() in ("", "$"):
        return data

    segments = _split_segments(path)
    current: Any = data
    for raw in segments:
        match = _SEGMENT_RE.match(raw)
        if not match:
            return None
        key, index_part = match.group(1), match.group(2)

        if isinstance(current, dict):
            if key not in current:
                return None
            current = current[key]
        else:
            return None

        if index_part:
            for idx_str in _INDEX_RE.findall(index_part):
                idx = int(idx_str)
                if not isinstance(current, list) or idx >= len(current) or idx < -len(current):
                    return None
                current = current[idx]
    return current


def set_path(target: dict[str, Any], path: str, value: Any) -> None:
    """Write ``value`` into ``target`` at the given dotted ``path``, creating
    intermediate dicts as needed.

    Used by the chain executor's ``inject`` step. Lists / array indices are
    NOT created -- the MVP only injects scalar / object values into named
    request fields like ``$.headers.Authorization`` or ``access_token``.
    Returns silently if ``path`` is empty.
    """
    if not path:
        return
    segments = _split_segments(path)
    if not segments:
        return

    cursor: dict[str, Any] = target
    for segment in segments[:-1]:
        match = _SEGMENT_RE.match(segment)
        if not match or match.group(2):
            # We don't synthesise list paths in the MVP -- skip silently.
            return
        key = match.group(1)
        next_node = cursor.get(key)
        if not isinstance(next_node, dict):
            next_node = {}
            cursor[key] = next_node
        cursor = next_node

    last = segments[-1]
    match = _SEGMENT_RE.match(last)
    if not match or match.group(2):
        return
    cursor[match.group(1)] = value
