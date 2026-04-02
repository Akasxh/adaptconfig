"""Comprehensive tests for ConfigValidator — full branch/edge-case coverage."""

from __future__ import annotations

from typing import Any

import pytest

from finspark.services.config_engine.validator import (
    MAX_RETRIES,
    MAX_TIMEOUT_MS,
    MIN_TIMEOUT_MS,
    VALID_AUTH_TYPES,
    VALID_HOOK_TYPES,
    VALID_HTTP_METHODS,
    ConfigValidator,
    ValidationReport,
    ValidationRuleResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_config() -> dict[str, Any]:
    """Minimal fully-valid configuration."""
    return {
        "adapter_name": "Test Adapter",
        "version": "v1",
        "base_url": "https://api.example.com/v1",
        "auth": {"type": "api_key", "key": "secret"},
        "endpoints": [{"path": "/data", "method": "GET"}],
        "field_mappings": [
            {"source_field": "id", "target_field": "external_id", "confidence": 1.0},
        ],
        "timeout_ms": 5000,
    }


@pytest.fixture
def v() -> ConfigValidator:
    return ConfigValidator()


# ---------------------------------------------------------------------------
# ValidationRuleResult dataclass
# ---------------------------------------------------------------------------

class TestValidationRuleResult:
    def test_is_frozen(self) -> None:
        r = ValidationRuleResult(rule_name="x", passed=True, message="ok", severity="info")
        with pytest.raises((AttributeError, TypeError)):
            r.passed = False  # type: ignore[misc]

    def test_fields_stored(self) -> None:
        r = ValidationRuleResult(rule_name="r", passed=False, message="msg", severity="error")
        assert r.rule_name == "r"
        assert r.passed is False
        assert r.message == "msg"
        assert r.severity == "error"


# ---------------------------------------------------------------------------
# ValidationReport dataclass
# ---------------------------------------------------------------------------

class TestValidationReport:
    def _make(self, *results: ValidationRuleResult) -> ValidationReport:
        return ValidationReport(results=list(results))

    def _err(self, passed: bool) -> ValidationRuleResult:
        return ValidationRuleResult(rule_name="r", passed=passed, message="", severity="error")

    def _warn(self, passed: bool) -> ValidationRuleResult:
        return ValidationRuleResult(rule_name="r", passed=passed, message="", severity="warning")

    def _info(self, passed: bool) -> ValidationRuleResult:
        return ValidationRuleResult(rule_name="r", passed=passed, message="", severity="info")

    def test_passed_all_ok(self) -> None:
        report = self._make(self._err(True), self._warn(True), self._info(True))
        assert report.passed is True

    def test_passed_false_on_error(self) -> None:
        report = self._make(self._err(False))
        assert report.passed is False

    def test_warning_does_not_fail_report(self) -> None:
        report = self._make(self._err(True), self._warn(False))
        assert report.passed is True

    def test_info_fail_does_not_fail_report(self) -> None:
        # An info-severity result that is not passed should not count as error
        report = self._make(self._info(False))
        assert report.passed is True

    def test_errors_property(self) -> None:
        report = self._make(self._err(False), self._err(True), self._warn(False))
        assert len(report.errors) == 1

    def test_warnings_property(self) -> None:
        report = self._make(self._err(False), self._warn(False), self._warn(True))
        assert len(report.warnings) == 1

    def test_empty_results(self) -> None:
        report = ValidationReport(results=[])
        assert report.passed is True
        assert report.errors == []
        assert report.warnings == []


# ---------------------------------------------------------------------------
# required_fields_mapped
# ---------------------------------------------------------------------------

class TestRequiredFieldsMapped:
    REQUIRED = ConfigValidator.REQUIRED_TOP_LEVEL

    def test_all_required_fields_present_passes(self, v: ConfigValidator) -> None:
        result = v.required_fields_mapped(_valid_config())
        assert result.passed
        assert result.severity == "info"
        assert result.rule_name == "required_fields_mapped"

    @pytest.mark.parametrize("field", list(ConfigValidator.REQUIRED_TOP_LEVEL))
    def test_each_missing_field_fails(self, v: ConfigValidator, field: str) -> None:
        config = _valid_config()
        del config[field]
        result = v.required_fields_mapped(config)
        assert not result.passed
        assert result.severity == "error"
        assert "Missing required fields" in result.message

    def test_empty_config_missing_all(self, v: ConfigValidator) -> None:
        result = v.required_fields_mapped({})
        assert not result.passed
        assert result.severity == "error"

    def test_message_lists_missing_fields_sorted(self, v: ConfigValidator) -> None:
        config = _valid_config()
        del config["auth"]
        del config["version"]
        result = v.required_fields_mapped(config)
        assert "auth" in result.message
        assert "version" in result.message

    def test_all_mappings_without_target_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["field_mappings"] = [
            {"source_field": "a", "target_field": ""},
            {"source_field": "b"},  # no target_field key at all
        ]
        result = v.required_fields_mapped(config)
        assert not result.passed
        assert "No source fields are mapped" in result.message
        assert result.severity == "error"

    def test_partial_mappings_passes(self, v: ConfigValidator) -> None:
        """One mapping with target_field + one without — should pass."""
        config = _valid_config()
        config["field_mappings"] = [
            {"source_field": "a", "target_field": "A"},
            {"source_field": "b", "target_field": ""},
        ]
        result = v.required_fields_mapped(config)
        assert result.passed

    def test_empty_field_mappings_list_passes(self, v: ConfigValidator) -> None:
        """Empty mappings list: unmapped list is also empty → condition False → passes."""
        config = _valid_config()
        config["field_mappings"] = []
        result = v.required_fields_mapped(config)
        assert result.passed

    def test_extra_top_level_fields_ignored(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["extra_key"] = "extra_value"
        config["another"] = 42
        result = v.required_fields_mapped(config)
        assert result.passed


# ---------------------------------------------------------------------------
# auth_configured
# ---------------------------------------------------------------------------

class TestAuthConfigured:
    @pytest.mark.parametrize("auth_type", sorted(VALID_AUTH_TYPES))
    def test_all_valid_auth_types_pass(self, v: ConfigValidator, auth_type: str) -> None:
        config = _valid_config()
        config["auth"] = {"type": auth_type}
        result = v.auth_configured(config)
        assert result.passed
        assert result.severity == "info"
        assert auth_type in result.message

    def test_auth_missing_key(self, v: ConfigValidator) -> None:
        config = _valid_config()
        del config["auth"]
        result = v.auth_configured(config)
        assert not result.passed
        assert result.severity == "error"
        assert "missing" in result.message.lower() or "not a dict" in result.message.lower()

    def test_auth_none_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["auth"] = None
        result = v.auth_configured(config)
        assert not result.passed
        assert result.severity == "error"

    def test_auth_string_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["auth"] = "api_key"
        result = v.auth_configured(config)
        assert not result.passed
        assert result.severity == "error"

    def test_auth_list_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["auth"] = [{"type": "api_key"}]
        result = v.auth_configured(config)
        assert not result.passed

    def test_unknown_auth_type_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["auth"] = {"type": "magic_token"}
        result = v.auth_configured(config)
        assert not result.passed
        assert result.severity == "error"
        assert "Unknown auth type" in result.message

    def test_empty_auth_type_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["auth"] = {"type": ""}
        result = v.auth_configured(config)
        assert not result.passed
        assert "Unknown auth type" in result.message

    def test_missing_type_key_in_auth_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["auth"] = {"credentials": "xyz"}  # no "type" key → defaults to ""
        result = v.auth_configured(config)
        assert not result.passed

    def test_case_sensitive_auth_type(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["auth"] = {"type": "API_KEY"}  # uppercase should fail
        result = v.auth_configured(config)
        assert not result.passed

    def test_valid_auth_types_set_completeness(self) -> None:
        assert VALID_AUTH_TYPES == {"api_key", "oauth2", "bearer", "basic", "jwt", "hmac"}


# ---------------------------------------------------------------------------
# endpoints_reachable
# ---------------------------------------------------------------------------

class TestEndpointsReachable:
    @pytest.mark.parametrize("method", sorted(VALID_HTTP_METHODS))
    def test_all_valid_methods_pass(self, v: ConfigValidator, method: str) -> None:
        config = _valid_config()
        config["endpoints"] = [{"path": "/test", "method": method}]
        result = v.endpoints_reachable(config)
        assert result.passed

    def test_no_endpoints_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["endpoints"] = []
        result = v.endpoints_reachable(config)
        assert not result.passed
        assert result.severity == "error"
        assert "No endpoints" in result.message

    def test_missing_endpoints_key_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        del config["endpoints"]
        result = v.endpoints_reachable(config)
        assert not result.passed
        assert result.severity == "error"

    def test_endpoint_not_a_dict_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["endpoints"] = ["not_a_dict"]
        result = v.endpoints_reachable(config)
        assert not result.passed
        assert result.severity == "error"
        assert "not a dict" in result.message

    def test_endpoint_at_index_1_not_dict(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["endpoints"] = [{"path": "/ok", "method": "GET"}, "bad"]
        result = v.endpoints_reachable(config)
        assert not result.passed
        assert "[1]" in result.message

    def test_empty_path_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["endpoints"] = [{"path": "", "method": "GET"}]
        result = v.endpoints_reachable(config)
        assert not result.passed
        assert result.severity == "error"

    def test_none_path_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["endpoints"] = [{"path": None, "method": "GET"}]
        result = v.endpoints_reachable(config)
        assert not result.passed
        assert result.severity == "error"

    def test_missing_path_key_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["endpoints"] = [{"method": "POST"}]  # path defaults to ""
        result = v.endpoints_reachable(config)
        assert not result.passed

    def test_invalid_method_is_warning(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["endpoints"] = [{"path": "/x", "method": "YEET"}]
        result = v.endpoints_reachable(config)
        assert not result.passed
        assert result.severity == "warning"
        assert "invalid method" in result.message

    def test_no_method_key_passes(self, v: ConfigValidator) -> None:
        """method is optional — if absent the check is skipped."""
        config = _valid_config()
        config["endpoints"] = [{"path": "/x"}]
        result = v.endpoints_reachable(config)
        assert result.passed

    def test_multiple_valid_endpoints(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["endpoints"] = [
            {"path": "/a", "method": "GET"},
            {"path": "/b", "method": "POST"},
            {"path": "/c", "method": "DELETE"},
        ]
        result = v.endpoints_reachable(config)
        assert result.passed
        assert "3" in result.message

    def test_method_case_insensitive(self, v: ConfigValidator) -> None:
        """Validator calls .upper() before checking, so lowercase methods pass."""
        config = _valid_config()
        config["endpoints"] = [{"path": "/x", "method": "get"}]
        result = v.endpoints_reachable(config)
        assert result.passed

    def test_valid_http_methods_set_completeness(self) -> None:
        assert VALID_HTTP_METHODS == {"GET", "POST", "PUT", "PATCH", "DELETE"}


# ---------------------------------------------------------------------------
# hooks_valid
# ---------------------------------------------------------------------------

class TestHooksValid:
    @pytest.mark.parametrize("hook_type", sorted(VALID_HOOK_TYPES))
    def test_all_valid_hook_types_pass(self, v: ConfigValidator, hook_type: str) -> None:
        config = _valid_config()
        config["hooks"] = [{"type": hook_type, "handler": "my_handler"}]
        result = v.hooks_valid(config)
        assert result.passed

    def test_no_hooks_key_passes(self, v: ConfigValidator) -> None:
        config = _valid_config()
        # hooks key absent — defaults to []
        result = v.hooks_valid(config)
        assert result.passed
        assert "No hooks" in result.message

    def test_empty_hooks_list_passes(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["hooks"] = []
        result = v.hooks_valid(config)
        assert result.passed

    def test_hook_not_dict_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["hooks"] = ["not_a_dict"]
        result = v.hooks_valid(config)
        assert not result.passed
        assert result.severity == "error"
        assert "not a dict" in result.message

    def test_hook_at_index_1_not_dict(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["hooks"] = [
            {"type": "pre_request", "handler": "h"},
            "bad_hook",
        ]
        result = v.hooks_valid(config)
        assert not result.passed
        assert "[1]" in result.message

    def test_invalid_hook_type_is_warning(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["hooks"] = [{"type": "invalid_phase", "handler": "h"}]
        result = v.hooks_valid(config)
        assert not result.passed
        assert result.severity == "warning"

    def test_missing_handler_is_error(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["hooks"] = [{"type": "pre_request"}]
        result = v.hooks_valid(config)
        assert not result.passed
        assert result.severity == "error"
        assert "missing a handler" in result.message

    def test_hook_type_via_hook_type_key(self, v: ConfigValidator) -> None:
        """Fallback: hook_type key instead of type key."""
        config = _valid_config()
        config["hooks"] = [{"hook_type": "on_error", "handler": "err_handler"}]
        result = v.hooks_valid(config)
        assert result.passed

    def test_both_type_and_hook_type_uses_type_first(self, v: ConfigValidator) -> None:
        """When both keys present, type takes precedence (or-chain)."""
        config = _valid_config()
        config["hooks"] = [{"type": "pre_request", "hook_type": "invalid", "handler": "h"}]
        result = v.hooks_valid(config)
        assert result.passed

    def test_multiple_valid_hooks(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["hooks"] = [
            {"type": "pre_request", "handler": "auth_inject"},
            {"type": "post_response", "handler": "log_response"},
            {"type": "on_error", "handler": "alert"},
        ]
        result = v.hooks_valid(config)
        assert result.passed
        assert "3" in result.message

    def test_valid_hook_types_set_completeness(self) -> None:
        assert VALID_HOOK_TYPES == {"pre_request", "post_response", "on_error", "on_timeout"}

    def test_empty_handler_string_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["hooks"] = [{"type": "pre_request", "handler": ""}]
        result = v.hooks_valid(config)
        # empty string is falsy → missing handler
        assert not result.passed
        assert result.severity == "error"

    def test_none_handler_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["hooks"] = [{"type": "pre_request", "handler": None}]
        result = v.hooks_valid(config)
        assert not result.passed
        assert result.severity == "error"


# ---------------------------------------------------------------------------
# retry_policy_valid
# ---------------------------------------------------------------------------

class TestRetryPolicyValid:
    def test_no_retry_policy_passes(self, v: ConfigValidator) -> None:
        config = _valid_config()
        # no retry_policy key
        result = v.retry_policy_valid(config)
        assert result.passed
        assert "No retry policy" in result.message

    def test_retry_policy_none_passes(self, v: ConfigValidator) -> None:
        """Explicit None is same as absent."""
        config = _valid_config()
        config["retry_policy"] = None
        result = v.retry_policy_valid(config)
        assert result.passed

    def test_retry_policy_not_dict_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = "3 times"
        result = v.retry_policy_valid(config)
        assert not result.passed
        assert result.severity == "error"
        assert "not a dict" in result.message

    def test_retry_policy_list_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = [3, 2.0]
        result = v.retry_policy_valid(config)
        assert not result.passed
        assert result.severity == "error"

    # Boundary: max_retries
    @pytest.mark.parametrize("retries", [0, 1, MAX_RETRIES])
    def test_max_retries_valid_boundary(self, v: ConfigValidator, retries: int) -> None:
        config = _valid_config()
        config["retry_policy"] = {"max_retries": retries, "backoff_factor": 1}
        result = v.retry_policy_valid(config)
        assert result.passed

    def test_max_retries_exceeds_max_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = {"max_retries": MAX_RETRIES + 1}
        result = v.retry_policy_valid(config)
        assert not result.passed
        assert result.severity == "error"

    def test_max_retries_negative_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = {"max_retries": -1}
        result = v.retry_policy_valid(config)
        assert not result.passed
        assert result.severity == "error"

    def test_max_retries_float_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = {"max_retries": 3.5}
        result = v.retry_policy_valid(config)
        assert not result.passed
        assert result.severity == "error"

    def test_max_retries_string_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = {"max_retries": "three"}
        result = v.retry_policy_valid(config)
        assert not result.passed

    def test_max_retries_defaults_to_zero_if_missing(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = {}  # max_retries defaults to 0 in the validator
        result = v.retry_policy_valid(config)
        assert result.passed

    # Boundary: backoff_factor
    def test_backoff_factor_zero_passes(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = {"max_retries": 3, "backoff_factor": 0}
        result = v.retry_policy_valid(config)
        assert result.passed

    def test_backoff_factor_float_passes(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = {"max_retries": 3, "backoff_factor": 1.5}
        result = v.retry_policy_valid(config)
        assert result.passed

    def test_backoff_factor_negative_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = {"max_retries": 3, "backoff_factor": -0.1}
        result = v.retry_policy_valid(config)
        assert not result.passed
        assert result.severity == "warning"

    def test_backoff_factor_string_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = {"max_retries": 3, "backoff_factor": "fast"}
        result = v.retry_policy_valid(config)
        assert not result.passed
        assert result.severity == "warning"

    def test_max_retries_constant_value(self) -> None:
        assert MAX_RETRIES == 10

    def test_valid_policy_message(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = {"max_retries": 3, "backoff_factor": 2}
        result = v.retry_policy_valid(config)
        assert result.passed
        assert "Retry policy valid" in result.message


# ---------------------------------------------------------------------------
# timeout_reasonable
# ---------------------------------------------------------------------------

class TestTimeoutReasonable:
    def test_timeout_at_min_boundary_passes(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["timeout_ms"] = MIN_TIMEOUT_MS
        result = v.timeout_reasonable(config)
        assert result.passed

    def test_timeout_at_max_boundary_passes(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["timeout_ms"] = MAX_TIMEOUT_MS
        result = v.timeout_reasonable(config)
        assert result.passed

    def test_timeout_just_below_min_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["timeout_ms"] = MIN_TIMEOUT_MS - 1
        result = v.timeout_reasonable(config)
        assert not result.passed
        assert result.severity == "warning"
        assert "below minimum" in result.message

    def test_timeout_just_above_max_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["timeout_ms"] = MAX_TIMEOUT_MS + 1
        result = v.timeout_reasonable(config)
        assert not result.passed
        assert result.severity == "warning"
        assert "exceeds maximum" in result.message

    def test_missing_timeout_is_warning(self, v: ConfigValidator) -> None:
        config = _valid_config()
        del config["timeout_ms"]
        result = v.timeout_reasonable(config)
        assert not result.passed
        assert result.severity == "warning"
        assert "not configured" in result.message

    def test_timeout_none_is_warning(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["timeout_ms"] = None
        result = v.timeout_reasonable(config)
        assert not result.passed
        assert result.severity == "warning"

    def test_timeout_string_is_error(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["timeout_ms"] = "fast"
        result = v.timeout_reasonable(config)
        assert not result.passed
        assert result.severity == "error"
        assert "must be a number" in result.message

    def test_timeout_list_is_error(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["timeout_ms"] = [5000]
        result = v.timeout_reasonable(config)
        assert not result.passed
        assert result.severity == "error"

    def test_timeout_float_passes(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["timeout_ms"] = 5000.5
        result = v.timeout_reasonable(config)
        assert result.passed

    def test_timeout_zero_fails(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["timeout_ms"] = 0
        result = v.timeout_reasonable(config)
        assert not result.passed
        assert result.severity == "warning"

    def test_timeout_valid_message(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["timeout_ms"] = 3000
        result = v.timeout_reasonable(config)
        assert result.passed
        assert "3000" in result.message

    def test_timeout_constants_values(self) -> None:
        assert MIN_TIMEOUT_MS == 100
        assert MAX_TIMEOUT_MS == 120_000


# ---------------------------------------------------------------------------
# validate_all — integration-level
# ---------------------------------------------------------------------------

class TestValidateAll:
    def test_full_valid_config_passes(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["hooks"] = [{"type": "pre_request", "handler": "logger"}]
        config["retry_policy"] = {"max_retries": 3, "backoff_factor": 2}
        report = v.validate_all(config)
        assert isinstance(report, ValidationReport)
        assert report.passed
        assert report.errors == []
        assert len(report.results) == 6

    def test_returns_exactly_six_results(self, v: ConfigValidator) -> None:
        report = v.validate_all(_valid_config())
        assert len(report.results) == 6

    def test_empty_config_fails(self, v: ConfigValidator) -> None:
        report = v.validate_all({})
        assert not report.passed
        assert len(report.errors) >= 1

    def test_only_required_fields_passes(self, v: ConfigValidator) -> None:
        """Minimal config — no optional fields."""
        report = v.validate_all(_valid_config())
        assert report.passed

    def test_extra_top_level_fields_do_not_break_validation(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["unknown_key"] = "unknown_value"
        config["nested_extra"] = {"a": 1}
        report = v.validate_all(config)
        assert report.passed

    def test_config_with_all_optional_fields_passes(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["hooks"] = [{"type": "on_timeout", "handler": "retry_handler"}]
        config["retry_policy"] = {"max_retries": 5, "backoff_factor": 1.5}
        report = v.validate_all(config)
        assert report.passed
        assert report.warnings == []

    def test_warning_only_config_passes(self, v: ConfigValidator) -> None:
        """timeout_ms missing → warning but not error → report.passed == True."""
        config = _valid_config()
        del config["timeout_ms"]
        report = v.validate_all(config)
        assert report.passed
        assert len(report.warnings) == 1

    def test_multiple_errors_collected(self, v: ConfigValidator) -> None:
        config: dict[str, Any] = {
            "adapter_name": "X",
            # missing: version, base_url, auth, endpoints, field_mappings
        }
        report = v.validate_all(config)
        assert not report.passed
        assert len(report.errors) >= 1

    def test_result_rule_names(self, v: ConfigValidator) -> None:
        report = v.validate_all(_valid_config())
        names = [r.rule_name for r in report.results]
        assert "required_fields_mapped" in names
        assert "auth_configured" in names
        assert "endpoints_reachable" in names
        assert "hooks_valid" in names
        assert "retry_policy_valid" in names
        assert "timeout_reasonable" in names

    def test_invalid_auth_in_full_run(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["auth"] = {"type": "unknown"}
        report = v.validate_all(config)
        assert not report.passed

    def test_invalid_endpoint_method_in_full_run(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["endpoints"] = [{"path": "/x", "method": "INVALID"}]
        report = v.validate_all(config)
        # warning from endpoint method — should not fail report
        assert report.passed
        assert len(report.warnings) >= 1

    def test_bad_retry_plus_bad_timeout_accumulates(self, v: ConfigValidator) -> None:
        config = _valid_config()
        config["retry_policy"] = {"max_retries": 999}
        config["timeout_ms"] = 999_999
        report = v.validate_all(config)
        assert not report.passed
        error_names = [r.rule_name for r in report.errors]
        assert "retry_policy_valid" in error_names
