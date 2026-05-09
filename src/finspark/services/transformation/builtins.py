"""Registry of built-in transformation functions."""

import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

_DATE_FORMATS = [
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y-%m-%d",
    "%d/%m/%y",
    "%m/%d/%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%d %b %Y",
    "%d %B %Y",
]


def _parse_number(value: Any) -> int | float:
    s = str(value).strip().replace(",", "")
    if "." in s:
        return float(s)
    return int(s)


def _parse_date(value: Any) -> str:
    s = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"Unable to parse date: {value!r}")


def _format_date(value: Any, fmt: str = "%d/%m/%Y") -> str:
    s = str(value).strip()
    dt = datetime.fromisoformat(s)
    return dt.strftime(fmt)


def _normalize_phone(value: Any) -> str:
    digits = re.sub(r"[^\d]", "", str(value))
    if digits.startswith("91") and len(digits) == 12:
        return f"+{digits}"
    if digits.startswith("0") and len(digits) == 11:
        return f"+91{digits[1:]}"
    if len(digits) == 10:
        return f"+91{digits}"
    raise ValueError(f"Unable to normalize phone: {value!r}")


def _validate_email(value: Any) -> str:
    s = str(value).strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", s):
        raise ValueError(f"Invalid email format: {value!r}")
    return s


def _parse_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in {"true", "1", "yes", "on"}:
        return True
    if s in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"Unable to parse boolean: {value!r}")


def _mask_aadhaar(value: Any) -> str:
    digits = re.sub(r"[^\d]", "", str(value))
    if len(digits) != 12:
        raise ValueError(f"Aadhaar must be 12 digits, got {len(digits)}")
    return f"XXXX-XXXX-{digits[8:]}"


def _mask_pan(value: Any) -> str:
    s = str(value).strip().upper()
    if len(s) != 10:
        raise ValueError(f"PAN must be 10 characters, got {len(s)}")
    return f"XXXXX****{s[-1]}"


def _paise_to_rupees(value: Any) -> float:
    return round(int(value) / 100, 2)


def _rupees_to_paise(value: Any) -> int:
    return round(float(value) * 100)


def _to_json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _from_json_string(value: Any) -> Any:
    return json.loads(str(value))


BUILTIN_TRANSFORMS: dict[str, Callable[..., Any]] = {
    "upper": lambda v: str(v).upper(),
    "lower": lambda v: str(v).lower(),
    "trim": lambda v: str(v).strip(),
    "parse_number": _parse_number,
    "to_string": lambda v: str(v),
    "parse_date": _parse_date,
    "format_date": _format_date,
    "normalize_phone": _normalize_phone,
    "validate_email": _validate_email,
    "parse_boolean": _parse_boolean,
    "mask_aadhaar": _mask_aadhaar,
    "mask_pan": _mask_pan,
    "paise_to_rupees": _paise_to_rupees,
    "rupees_to_paise": _rupees_to_paise,
    "to_json_string": _to_json_string,
    "from_json_string": _from_json_string,
}
