"""Field-level payload transformation.

Resolves the "labels-as-metadata" bug: until this module landed, a mapping
with `transformation: "upper"` was just a string written into JSON. Nothing
upper-cased anything. This module turns those labels into functions.

Public API:
    BUILTIN_TRANSFORMS — name -> Callable[[Any], Any]
    transform_payload(source, mappings) -> {payload, results, success}

No sandboxing, no AST validation, no chained transforms. Each mapping names
exactly one builtin. If a transform raises, that field records the error and
keeps the original value; the run as a whole still returns a payload.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

_DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y", "%m/%d/%Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    "%d %b %Y", "%d %B %Y",
]


def _parse_number(value: Any) -> int | float:
    s = str(value).strip().replace(",", "")
    return float(s) if "." in s else int(s)


def _parse_date(value: Any) -> str:
    s = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"unable to parse date: {value!r}")


def _normalize_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value))
    if digits.startswith("91") and len(digits) == 12:
        return f"+{digits}"
    if digits.startswith("0") and len(digits) == 11:
        return f"+91{digits[1:]}"
    if len(digits) == 10:
        return f"+91{digits}"
    raise ValueError(f"unable to normalize phone: {value!r}")


def _mask_aadhaar(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value))
    if len(digits) != 12:
        raise ValueError(f"aadhaar must be 12 digits: {value!r}")
    return f"XXXX-XXXX-{digits[-4:]}"


def _paise_to_rupees(value: Any) -> float:
    return round(int(value) / 100, 2)


def _rupees_to_paise(value: Any) -> int:
    return int(round(float(value) * 100))


BUILTIN_TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "upper":           lambda v: str(v).upper(),
    "lower":           lambda v: str(v).lower(),
    "trim":            lambda v: str(v).strip(),
    "parse_number":    _parse_number,
    "parse_date":      _parse_date,
    "normalize_phone": _normalize_phone,
    "mask_aadhaar":    _mask_aadhaar,
    "paise_to_rupees": _paise_to_rupees,
    "rupees_to_paise": _rupees_to_paise,
}


def transform_payload(
    source: dict[str, Any],
    mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply field_mappings to a source payload, return target + per-field log.

    For each mapping with a known transformation, look it up in BUILTIN_TRANSFORMS
    and call it on source[source_field]. Statuses per field:
        transformed — function ran and returned a new value
        passthrough — no transformation declared or unknown name; value copied as-is
        missing     — source_field not present in source payload
        error       — function raised; original value copied through, error recorded
    """
    target: dict[str, Any] = {}
    results: list[dict[str, Any]] = []
    error_count = 0

    for m in mappings:
        src_field = m.get("source_field")
        tgt_field = m.get("target_field")
        if not src_field or not tgt_field:
            continue

        if src_field not in source:
            results.append({
                "source_field": src_field, "target_field": tgt_field,
                "status": "missing", "original": None, "transformed": None,
                "transformation": m.get("transformation"), "error": None,
            })
            continue

        original = source[src_field]
        name = m.get("transformation")
        fn = BUILTIN_TRANSFORMS.get(name) if name else None

        if fn is None:
            target[tgt_field] = original
            results.append({
                "source_field": src_field, "target_field": tgt_field,
                "status": "passthrough", "original": original, "transformed": original,
                "transformation": name, "error": None,
            })
            continue

        try:
            transformed = fn(original)
            target[tgt_field] = transformed
            results.append({
                "source_field": src_field, "target_field": tgt_field,
                "status": "transformed", "original": original, "transformed": transformed,
                "transformation": name, "error": None,
            })
        except Exception as exc:  # noqa: BLE001 — transform errors are expected at runtime
            target[tgt_field] = original
            error_count += 1
            results.append({
                "source_field": src_field, "target_field": tgt_field,
                "status": "error", "original": original, "transformed": original,
                "transformation": name, "error": str(exc),
            })

    return {
        "payload": target,
        "results": results,
        "success": error_count == 0,
        "error_count": error_count,
    }
