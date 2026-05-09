"""Tests for confidence-driven tiered validation."""

from __future__ import annotations

from typing import Any

import pytest

from finspark.services.config_engine.validator import (
    ConfigValidator,
    FieldReviewFlag,
    TieredValidationResult,
    ValidationReport,
)


def _base_config(**overrides: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "adapter_name": "KYC Provider",
        "version": "v1",
        "base_url": "https://api.kyc.in/v1",
        "auth": {"type": "api_key", "credentials": {}},
        "endpoints": [{"path": "/verify", "method": "POST"}],
        "field_mappings": [
            {"source_field": "pan", "target_field": "pan_number", "confidence": 0.95},
        ],
        "hooks": [
            {"name": "log", "type": "pre_request", "handler": "audit_logger"},
        ],
        "retry_policy": {"max_retries": 3, "backoff_factor": 2},
        "timeout_ms": 30000,
    }
    config.update(overrides)
    return config


@pytest.fixture
def validator() -> ConfigValidator:
    return ConfigValidator()


class TestFastTrackHighConfidence:
    def test_fast_track_high_confidence(self, validator: ConfigValidator) -> None:
        config = _base_config(field_mappings=[
            {"source_field": "pan", "target_field": "pan_number", "confidence": 0.95},
            {"source_field": "name", "target_field": "full_name", "confidence": 0.90},
            {"source_field": "dob", "target_field": "date_of_birth", "confidence": 0.88},
        ])
        result = validator.validate_with_confidence(config)

        assert result.passed is True
        assert result.strategy_used == "fast_track"
        assert all(f.action == "auto_approved" for f in result.field_flags)
        assert result.auto_approved_count == 3
        assert result.needs_review_count == 0
        assert result.review_required is False


class TestStandardMediumConfidence:
    def test_standard_medium_confidence(self, validator: ConfigValidator) -> None:
        config = _base_config(field_mappings=[
            {"source_field": "pan", "target_field": "pan_number", "confidence": 0.90},
            {"source_field": "name", "target_field": "full_name", "confidence": 0.60},
            {"source_field": "dob", "target_field": "date_of_birth", "confidence": 0.55},
        ])
        result = validator.validate_with_confidence(config)

        assert result.passed is True
        assert result.strategy_used == "standard"
        actions = {f.field_mapping: f.action for f in result.field_flags}
        assert actions["pan → pan_number"] == "auto_approved"
        assert actions["name → full_name"] == "needs_review"
        assert actions["dob → date_of_birth"] == "needs_review"


class TestDeepReviewLowConfidence:
    def test_deep_review_low_confidence(self, validator: ConfigValidator) -> None:
        config = _base_config(field_mappings=[
            {"source_field": "pan", "target_field": "pan_number", "confidence": 0.30},
            {"source_field": "name", "target_field": "full_name", "confidence": 0.20},
            {"source_field": "dob", "target_field": "date_of_birth", "confidence": 0.40},
        ])
        result = validator.validate_with_confidence(config)

        assert result.strategy_used == "deep_review"
        assert result.review_required is True
        assert all(f.action == "low_confidence" for f in result.field_flags)
        assert result.needs_review_count == 3
        assert result.auto_approved_count == 0


class TestMixedFieldConfidence:
    def test_mixed_field_confidence(self, validator: ConfigValidator) -> None:
        config = _base_config(field_mappings=[
            {"source_field": "pan", "target_field": "pan_number", "confidence": 0.95},
            {"source_field": "name", "target_field": "full_name", "confidence": 0.70},
            {"source_field": "dob", "target_field": "date_of_birth", "confidence": 0.40},
        ])
        result = validator.validate_with_confidence(config)

        assert result.strategy_used == "standard"
        flag_map = {f.field_mapping: f for f in result.field_flags}
        assert flag_map["pan → pan_number"].action == "auto_approved"
        assert flag_map["name → full_name"].action == "needs_review"
        assert flag_map["dob → date_of_birth"].action == "low_confidence"


class TestFastTrackStillCatchesMissingFields:
    def test_fast_track_still_catches_missing_fields(self, validator: ConfigValidator) -> None:
        config = _base_config(field_mappings=[
            {"source_field": "pan", "target_field": "pan_number", "confidence": 0.95},
        ])
        del config["base_url"]
        result = validator.validate_with_confidence(config)

        assert result.passed is False
        assert result.strategy_used == "fast_track"
        assert any("Missing required fields" in e for e in result.errors)


class TestBackwardCompatValidate:
    def test_backward_compat_validate(self, validator: ConfigValidator) -> None:
        config = _base_config()
        report = validator.validate_all(config)

        assert isinstance(report, ValidationReport)
        assert report.passed is True
        assert len(report.results) == 6


class TestNoConfidenceDefaultsToStandard:
    def test_no_confidence_defaults_to_standard(self, validator: ConfigValidator) -> None:
        config = _base_config(field_mappings=[
            {"source_field": "pan", "target_field": "pan_number"},
            {"source_field": "name", "target_field": "full_name"},
        ])
        result = validator.validate_with_confidence(config)

        assert result.strategy_used == "deep_review"
        assert all(f.action == "low_confidence" for f in result.field_flags)


class TestEmptyFieldMappings:
    def test_empty_field_mappings(self, validator: ConfigValidator) -> None:
        config = _base_config(field_mappings=[])
        result = validator.validate_with_confidence(config)

        assert isinstance(result, TieredValidationResult)
        assert result.strategy_used == "deep_review"
        assert result.field_flags == []
        assert result.auto_approved_count == 0
        assert result.needs_review_count == 0
