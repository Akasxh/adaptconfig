"""Tests for the field-level transformation service (issue #113)."""

from __future__ import annotations

import pytest

from finspark.services.transformation import BUILTIN_TRANSFORMS, transform_payload


# ── Builtin behaviour ────────────────────────────────────────────────────────


class TestBuiltins:
    def test_upper_lower_trim(self) -> None:
        assert BUILTIN_TRANSFORMS["upper"]("abc") == "ABC"
        assert BUILTIN_TRANSFORMS["lower"]("ABC") == "abc"
        assert BUILTIN_TRANSFORMS["trim"]("  hi  ") == "hi"

    def test_parse_number_int_and_float_and_commas(self) -> None:
        assert BUILTIN_TRANSFORMS["parse_number"]("50000") == 50000
        assert BUILTIN_TRANSFORMS["parse_number"]("50,000") == 50000
        assert BUILTIN_TRANSFORMS["parse_number"]("1234.56") == 1234.56
        assert BUILTIN_TRANSFORMS["parse_number"]("  42 ") == 42

    def test_parse_number_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            BUILTIN_TRANSFORMS["parse_number"]("abc")

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("15/05/1990", "1990-05-15"),
            ("1990-05-15", "1990-05-15"),
            ("15-05-1990", "1990-05-15"),
            ("15 May 1990", "1990-05-15"),
        ],
    )
    def test_parse_date_formats(self, raw: str, expected: str) -> None:
        assert BUILTIN_TRANSFORMS["parse_date"](raw) == expected

    def test_parse_date_rejects_unknown(self) -> None:
        with pytest.raises(ValueError):
            BUILTIN_TRANSFORMS["parse_date"]("not a date")

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("9876543210", "+919876543210"),
            ("919876543210", "+919876543210"),
            ("09876543210", "+919876543210"),
            ("+91 98765 43210", "+919876543210"),
        ],
    )
    def test_normalize_phone(self, raw: str, expected: str) -> None:
        assert BUILTIN_TRANSFORMS["normalize_phone"](raw) == expected

    def test_normalize_phone_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            BUILTIN_TRANSFORMS["normalize_phone"]("abc")

    def test_mask_aadhaar(self) -> None:
        assert BUILTIN_TRANSFORMS["mask_aadhaar"]("123456789012") == "XXXX-XXXX-9012"
        assert BUILTIN_TRANSFORMS["mask_aadhaar"]("1234 5678 9012") == "XXXX-XXXX-9012"

    def test_mask_aadhaar_rejects_wrong_length(self) -> None:
        with pytest.raises(ValueError):
            BUILTIN_TRANSFORMS["mask_aadhaar"]("12345")

    def test_paise_rupees_round_trip(self) -> None:
        assert BUILTIN_TRANSFORMS["paise_to_rupees"](5_000_000) == 50000.0
        assert BUILTIN_TRANSFORMS["rupees_to_paise"](50000.0) == 5_000_000
        # rupees_to_paise must handle string input from JSON
        assert BUILTIN_TRANSFORMS["rupees_to_paise"]("12.34") == 1234


# ── transform_payload orchestration ──────────────────────────────────────────


class TestTransformPayload:
    def test_applies_known_transforms(self) -> None:
        out = transform_payload(
            {"pan": "abcde1234f", "amt": "50,000"},
            [
                {"source_field": "pan", "target_field": "PAN", "transformation": "upper"},
                {"source_field": "amt", "target_field": "amount", "transformation": "parse_number"},
            ],
        )
        assert out["payload"] == {"PAN": "ABCDE1234F", "amount": 50000}
        assert out["success"] is True
        assert out["error_count"] == 0
        assert [r["status"] for r in out["results"]] == ["transformed", "transformed"]

    def test_unknown_transform_name_is_passthrough(self) -> None:
        """LLM sometimes invents transform names. They must not crash — just copy."""
        out = transform_payload(
            {"x": "raw"},
            [{"source_field": "x", "target_field": "y", "transformation": "mask_last_8_in_logs"}],
        )
        assert out["payload"] == {"y": "raw"}
        assert out["results"][0]["status"] == "passthrough"

    def test_no_transformation_is_passthrough(self) -> None:
        out = transform_payload(
            {"x": 42},
            [{"source_field": "x", "target_field": "y", "transformation": None}],
        )
        assert out["payload"] == {"y": 42}
        assert out["results"][0]["status"] == "passthrough"

    def test_missing_source_field(self) -> None:
        out = transform_payload(
            {"a": 1},
            [{"source_field": "b", "target_field": "y", "transformation": "upper"}],
        )
        assert out["payload"] == {}  # nothing written
        assert out["results"][0]["status"] == "missing"
        assert out["success"] is True  # missing is not an error

    def test_transform_error_keeps_original_and_marks_failure(self) -> None:
        out = transform_payload(
            {"phone": "abc"},
            [{"source_field": "phone", "target_field": "phone", "transformation": "normalize_phone"}],
        )
        assert out["payload"] == {"phone": "abc"}  # original preserved
        assert out["results"][0]["status"] == "error"
        assert "unable to normalize" in out["results"][0]["error"]
        assert out["success"] is False
        assert out["error_count"] == 1

    def test_partial_failure_one_run(self) -> None:
        """One bad field doesn't poison the rest of the run."""
        out = transform_payload(
            {"pan": "abc", "phone": "garbage"},
            [
                {"source_field": "pan", "target_field": "pan", "transformation": "upper"},
                {"source_field": "phone", "target_field": "phone", "transformation": "normalize_phone"},
            ],
        )
        assert out["payload"]["pan"] == "ABC"          # good one ran
        assert out["payload"]["phone"] == "garbage"    # bad one preserved
        assert out["error_count"] == 1
        assert out["success"] is False

    def test_skips_mappings_with_missing_keys(self) -> None:
        """Defensive: malformed mapping entries shouldn't crash the run."""
        out = transform_payload(
            {"x": 1},
            [
                {"target_field": "y"},                  # no source_field
                {"source_field": "x"},                  # no target_field
                {"source_field": "x", "target_field": "z"},
            ],
        )
        assert out["payload"] == {"z": 1}
        assert len(out["results"]) == 1
