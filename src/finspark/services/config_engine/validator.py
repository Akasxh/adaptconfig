"""Configuration validator - validates generated integration configs against rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

VALID_AUTH_TYPES = {"api_key", "oauth2", "bearer", "basic", "jwt", "hmac"}
VALID_HOOK_TYPES = {"pre_request", "post_response", "on_error", "on_timeout"}
VALID_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

MAX_TIMEOUT_MS = 120_000
MIN_TIMEOUT_MS = 100
MAX_RETRIES = 10

FAST_TRACK_THRESHOLD = 0.85
NEEDS_REVIEW_THRESHOLD = 0.5
FIELD_REVIEW_THRESHOLD = 0.7


class ValidationStrategy(str, Enum):
    FAST_TRACK = "fast_track"
    STANDARD = "standard"
    DEEP_REVIEW = "deep_review"


@dataclass
class FieldReviewFlag:
    field_mapping: str
    confidence: float
    action: str
    reason: str | None = None


@dataclass
class TieredValidationResult:
    passed: bool
    strategy_used: str
    errors: list[str]
    warnings: list[str]
    field_flags: list[FieldReviewFlag]
    review_required: bool
    auto_approved_count: int
    needs_review_count: int


@dataclass(frozen=True)
class ValidationRuleResult:
    """Result of a single validation rule check."""

    rule_name: str
    passed: bool
    message: str
    severity: Literal["error", "warning", "info"]


@dataclass(frozen=True)
class ValidationReport:
    """Full validation report across all rules."""

    results: list[ValidationRuleResult]

    @property
    def passed(self) -> bool:
        return not any(r.severity == "error" and not r.passed for r in self.results)

    @property
    def errors(self) -> list[ValidationRuleResult]:
        return [r for r in self.results if not r.passed and r.severity == "error"]

    @property
    def warnings(self) -> list[ValidationRuleResult]:
        return [r for r in self.results if not r.passed and r.severity == "warning"]


class ConfigValidator:
    """Validates a generated integration configuration dict."""

    REQUIRED_TOP_LEVEL = {
        "adapter_name",
        "version",
        "base_url",
        "auth",
        "endpoints",
        "field_mappings",
    }

    def validate_all(self, config: dict[str, Any]) -> ValidationReport:
        """Run all validation rules and return a full report."""
        results = [
            self.required_fields_mapped(config),
            self.auth_configured(config),
            self.endpoints_reachable(config),
            self.hooks_valid(config),
            self.retry_policy_valid(config),
            self.timeout_reasonable(config),
        ]
        return ValidationReport(results=results)

    def required_fields_mapped(self, config: dict[str, Any]) -> ValidationRuleResult:
        """Check that all required top-level fields are present."""
        missing = self.REQUIRED_TOP_LEVEL - set(config.keys())
        if missing:
            return ValidationRuleResult(
                rule_name="required_fields_mapped",
                passed=False,
                message=f"Missing required fields: {sorted(missing)}",
                severity="error",
            )
        # Check field_mappings has at least one confirmed or non-empty mapping
        mappings = config.get("field_mappings", [])
        unmapped = [m for m in mappings if not m.get("target_field")]
        if unmapped and len(unmapped) == len(mappings):
            return ValidationRuleResult(
                rule_name="required_fields_mapped",
                passed=False,
                message="No source fields are mapped to target fields",
                severity="error",
            )
        return ValidationRuleResult(
            rule_name="required_fields_mapped",
            passed=True,
            message="All required fields present",
            severity="info",
        )

    def auth_configured(self, config: dict[str, Any]) -> ValidationRuleResult:
        """Validate that auth section is present and has a known type."""
        auth = config.get("auth")
        if not isinstance(auth, dict):
            return ValidationRuleResult(
                rule_name="auth_configured",
                passed=False,
                message="Auth section missing or not a dict",
                severity="error",
            )
        auth_type = auth.get("type", "")
        if auth_type not in VALID_AUTH_TYPES:
            return ValidationRuleResult(
                rule_name="auth_configured",
                passed=False,
                message=f"Unknown auth type '{auth_type}'. Valid: {sorted(VALID_AUTH_TYPES)}",
                severity="error",
            )
        return ValidationRuleResult(
            rule_name="auth_configured",
            passed=True,
            message=f"Auth configured with type '{auth_type}'",
            severity="info",
        )

    def endpoints_reachable(self, config: dict[str, Any]) -> ValidationRuleResult:
        """Validate endpoints have valid paths and methods."""
        endpoints = config.get("endpoints", [])
        if not endpoints:
            return ValidationRuleResult(
                rule_name="endpoints_reachable",
                passed=False,
                message="No endpoints configured",
                severity="error",
            )
        for i, ep in enumerate(endpoints):
            if not isinstance(ep, dict):
                return ValidationRuleResult(
                    rule_name="endpoints_reachable",
                    passed=False,
                    message=f"Endpoint [{i}] is not a dict",
                    severity="error",
                )
            path = ep.get("path", "")
            if not path or not isinstance(path, str):
                return ValidationRuleResult(
                    rule_name="endpoints_reachable",
                    passed=False,
                    message=f"Endpoint [{i}] has empty or invalid path",
                    severity="error",
                )
            method = ep.get("method", "")
            if method and method.upper() not in VALID_HTTP_METHODS:
                return ValidationRuleResult(
                    rule_name="endpoints_reachable",
                    passed=False,
                    message=f"Endpoint [{i}] has invalid method '{method}'",
                    severity="warning",
                )
        return ValidationRuleResult(
            rule_name="endpoints_reachable",
            passed=True,
            message=f"{len(endpoints)} endpoint(s) configured",
            severity="info",
        )

    def hooks_valid(self, config: dict[str, Any]) -> ValidationRuleResult:
        """Validate hook configurations if present."""
        hooks = config.get("hooks", [])
        if not hooks:
            return ValidationRuleResult(
                rule_name="hooks_valid",
                passed=True,
                message="No hooks configured",
                severity="info",
            )
        for i, hook in enumerate(hooks):
            if not isinstance(hook, dict):
                return ValidationRuleResult(
                    rule_name="hooks_valid",
                    passed=False,
                    message=f"Hook [{i}] is not a dict",
                    severity="error",
                )
            hook_type = hook.get("type", hook.get("hook_type", ""))
            if hook_type not in VALID_HOOK_TYPES:
                return ValidationRuleResult(
                    rule_name="hooks_valid",
                    passed=False,
                    message=f"Hook [{i}] has invalid type '{hook_type}'",
                    severity="warning",
                )
            if not hook.get("handler"):
                return ValidationRuleResult(
                    rule_name="hooks_valid",
                    passed=False,
                    message=f"Hook [{i}] is missing a handler",
                    severity="error",
                )
        return ValidationRuleResult(
            rule_name="hooks_valid",
            passed=True,
            message=f"{len(hooks)} hook(s) valid",
            severity="info",
        )

    def retry_policy_valid(self, config: dict[str, Any]) -> ValidationRuleResult:
        """Validate retry policy settings."""
        policy = config.get("retry_policy")
        if policy is None:
            return ValidationRuleResult(
                rule_name="retry_policy_valid",
                passed=True,
                message="No retry policy configured",
                severity="info",
            )
        if not isinstance(policy, dict):
            return ValidationRuleResult(
                rule_name="retry_policy_valid",
                passed=False,
                message="retry_policy is not a dict",
                severity="error",
            )
        max_retries = policy.get("max_retries", 0)
        if not isinstance(max_retries, int) or max_retries < 0 or max_retries > MAX_RETRIES:
            return ValidationRuleResult(
                rule_name="retry_policy_valid",
                passed=False,
                message=f"max_retries must be an int in [0, {MAX_RETRIES}], got {max_retries}",
                severity="error",
            )
        backoff = policy.get("backoff_factor", 1)
        if not isinstance(backoff, (int, float)) or backoff < 0:
            return ValidationRuleResult(
                rule_name="retry_policy_valid",
                passed=False,
                message=f"backoff_factor must be a non-negative number, got {backoff}",
                severity="warning",
            )
        return ValidationRuleResult(
            rule_name="retry_policy_valid",
            passed=True,
            message="Retry policy valid",
            severity="info",
        )

    def timeout_reasonable(self, config: dict[str, Any]) -> ValidationRuleResult:
        """Check that timeout_ms is within a reasonable range."""
        timeout = config.get("timeout_ms")
        if timeout is None:
            return ValidationRuleResult(
                rule_name="timeout_reasonable",
                passed=False,
                message="timeout_ms not configured",
                severity="warning",
            )
        if not isinstance(timeout, (int, float)):
            return ValidationRuleResult(
                rule_name="timeout_reasonable",
                passed=False,
                message=f"timeout_ms must be a number, got {type(timeout).__name__}",
                severity="error",
            )
        if timeout < MIN_TIMEOUT_MS:
            return ValidationRuleResult(
                rule_name="timeout_reasonable",
                passed=False,
                message=f"timeout_ms={timeout} is below minimum {MIN_TIMEOUT_MS}ms",
                severity="warning",
            )
        if timeout > MAX_TIMEOUT_MS:
            return ValidationRuleResult(
                rule_name="timeout_reasonable",
                passed=False,
                message=f"timeout_ms={timeout} exceeds maximum {MAX_TIMEOUT_MS}ms",
                severity="warning",
            )
        return ValidationRuleResult(
            rule_name="timeout_reasonable",
            passed=True,
            message=f"Timeout {timeout}ms is reasonable",
            severity="info",
        )

    def validate_with_confidence(self, config: dict[str, Any]) -> TieredValidationResult:
        """Run confidence-aware validation with tiered strategy selection."""
        mappings = config.get("field_mappings", [])
        avg_confidence = _compute_avg_confidence(mappings)
        strategy = _select_strategy(avg_confidence)
        field_flags = _build_field_flags(mappings, strategy)

        errors, warnings = self._run_checks_for_strategy(config, strategy)

        review_required = strategy == ValidationStrategy.DEEP_REVIEW or any(
            f.action == "needs_review" for f in field_flags
        )
        auto_approved = sum(1 for f in field_flags if f.action == "auto_approved")
        needs_review = sum(1 for f in field_flags if f.action != "auto_approved")

        return TieredValidationResult(
            passed=len(errors) == 0,
            strategy_used=strategy.value,
            errors=errors,
            warnings=warnings,
            field_flags=field_flags,
            review_required=review_required,
            auto_approved_count=auto_approved,
            needs_review_count=needs_review,
        )

    def _run_checks_for_strategy(
        self, config: dict[str, Any], strategy: ValidationStrategy
    ) -> tuple[list[str], list[str]]:
        """Run validation checks appropriate for the given strategy."""
        errors: list[str] = []
        warnings: list[str] = []

        structural_checks = [
            self.required_fields_mapped(config),
            self.auth_configured(config),
            self.retry_policy_valid(config),
            self.timeout_reasonable(config),
        ]
        for result in structural_checks:
            if not result.passed and result.severity == "error":
                errors.append(result.message)
            elif not result.passed and result.severity == "warning":
                warnings.append(result.message)

        if strategy in (ValidationStrategy.STANDARD, ValidationStrategy.DEEP_REVIEW):
            extra_checks = [
                self.endpoints_reachable(config),
                self.hooks_valid(config),
            ]
            for result in extra_checks:
                if not result.passed and result.severity == "error":
                    errors.append(result.message)
                elif not result.passed and result.severity == "warning":
                    warnings.append(result.message)

        return errors, warnings


def _compute_avg_confidence(mappings: list[dict[str, Any]]) -> float:
    if not mappings:
        return 0.0
    confidences = [float(m.get("confidence", 0.0)) for m in mappings]
    return sum(confidences) / len(confidences)


def _select_strategy(avg_confidence: float) -> ValidationStrategy:
    if avg_confidence > FAST_TRACK_THRESHOLD:
        return ValidationStrategy.FAST_TRACK
    if avg_confidence >= NEEDS_REVIEW_THRESHOLD:
        return ValidationStrategy.STANDARD
    return ValidationStrategy.DEEP_REVIEW


def _build_field_flags(
    mappings: list[dict[str, Any]], strategy: ValidationStrategy
) -> list[FieldReviewFlag]:
    flags: list[FieldReviewFlag] = []
    for m in mappings:
        source = m.get("source_field", "?")
        target = m.get("target_field", "?")
        confidence = float(m.get("confidence", 0.0))
        label = f"{source} → {target}" if target else source
        flags.append(_classify_field(label, confidence, strategy))
    return flags


def _classify_field(
    label: str, confidence: float, strategy: ValidationStrategy
) -> FieldReviewFlag:
    if strategy == ValidationStrategy.DEEP_REVIEW:
        if confidence >= FAST_TRACK_THRESHOLD:
            return FieldReviewFlag(label, confidence, "auto_approved")
        if confidence >= NEEDS_REVIEW_THRESHOLD:
            return FieldReviewFlag(
                label, confidence, "needs_review",
                reason=f"Confidence {confidence:.2f} is below auto-approve threshold in deep review",
            )
        return FieldReviewFlag(
            label, confidence, "low_confidence",
            reason=f"Confidence {confidence:.2f} is below acceptable threshold",
        )

    if confidence >= FAST_TRACK_THRESHOLD:
        return FieldReviewFlag(label, confidence, "auto_approved")
    if confidence >= NEEDS_REVIEW_THRESHOLD:
        return FieldReviewFlag(
            label, confidence, "needs_review",
            reason=f"Confidence {confidence:.2f} is below {FAST_TRACK_THRESHOLD} threshold",
        )
    return FieldReviewFlag(
        label, confidence, "low_confidence",
        reason=f"Confidence {confidence:.2f} is below acceptable threshold",
    )
