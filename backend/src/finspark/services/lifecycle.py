"""
Configuration lifecycle state machine.

Valid transitions:
    draft       → configured   (config fields populated)
    configured  → validating   (gate: base_url, auth, endpoints present)
    validating  → testing      (validation passed)
    testing     → active       (gate: simulation results exist and passed; admin only)
    active      → deprecated   (admin only; audited)
    any         → draft        (rollback — only when not active/deprecated)

Rollback is only permitted from: configured, validating, testing.
"""
from __future__ import annotations

import logging
from typing import Any

from finspark.schemas.configurations import ConfigStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowed forward transitions (source → set of valid targets)
# ---------------------------------------------------------------------------

ALLOWED_TRANSITIONS: dict[ConfigStatus, set[ConfigStatus]] = {
    ConfigStatus.DRAFT: {ConfigStatus.CONFIGURED},
    ConfigStatus.CONFIGURED: {ConfigStatus.VALIDATING},
    ConfigStatus.VALIDATING: {ConfigStatus.TESTING},
    ConfigStatus.TESTING: {ConfigStatus.ACTIVE},
    ConfigStatus.ACTIVE: {ConfigStatus.DEPRECATED},
    ConfigStatus.DEPRECATED: set(),
    ConfigStatus.FAILED: {ConfigStatus.DRAFT},
}

# States from which rollback (→ draft) is permitted
ROLLBACK_ALLOWED_FROM: set[ConfigStatus] = {
    ConfigStatus.CONFIGURED,
    ConfigStatus.VALIDATING,
    ConfigStatus.TESTING,
}

# Transitions that require admin role
ADMIN_ONLY_TARGETS: set[ConfigStatus] = {
    ConfigStatus.ACTIVE,
    ConfigStatus.DEPRECATED,
}


# ---------------------------------------------------------------------------
# Gate guards
# ---------------------------------------------------------------------------


def _check_configured_to_validating(payload: dict[str, Any]) -> list[str]:
    """Gate for configured → validating: payload must have base_url, auth, endpoints."""
    errors: list[str] = []
    if not payload.get("base_url"):
        errors.append("payload.base_url is required before validation")
    if not payload.get("auth"):
        errors.append("payload.auth is required before validation")
    if not payload.get("endpoints"):
        errors.append("payload.endpoints must be a non-empty list before validation")
    return errors


def _check_testing_to_active(simulation_results: dict[str, Any] | None) -> list[str]:
    """Gate for testing → active: simulation results must exist and have passed."""
    if not simulation_results:
        return ["simulation results are required before activating"]
    if not simulation_results.get("passed"):
        return ["simulation must have passed before activating (simulation_results.passed is falsy)"]
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class InvalidTransitionError(ValueError):
    """Raised when a requested state transition is not permitted."""


class InsufficientRoleError(PermissionError):
    """Raised when the caller lacks the required role for a transition."""


def validate_transition(
    current_status: ConfigStatus,
    target_status: ConfigStatus,
    *,
    payload: dict[str, Any] | None = None,
    simulation_results: dict[str, Any] | None = None,
    caller_roles: frozenset[str] | None = None,
) -> None:
    """
    Assert that the transition from *current_status* to *target_status* is valid.

    Raises:
        InvalidTransitionError: transition not in the state machine or gate check failed.
        InsufficientRoleError: admin-only transition attempted without admin role.
    """
    allowed = ALLOWED_TRANSITIONS.get(current_status, set())
    if target_status not in allowed:
        raise InvalidTransitionError(
            f"Cannot transition from {current_status.value!r} to {target_status.value!r}. "
            f"Allowed targets: {[s.value for s in allowed] or 'none'}"
        )

    # Role check before gate checks (fail fast)
    if target_status in ADMIN_ONLY_TARGETS:
        roles = caller_roles or frozenset()
        if not (roles & {"admin", "superadmin"}):
            raise InsufficientRoleError(
                f"Transition to {target_status.value!r} requires admin or superadmin role."
            )

    # Gate guards
    if current_status == ConfigStatus.CONFIGURED and target_status == ConfigStatus.VALIDATING:
        errors = _check_configured_to_validating(payload or {})
        if errors:
            raise InvalidTransitionError(
                f"Gate check failed for configured→validating: {'; '.join(errors)}"
            )

    if current_status == ConfigStatus.TESTING and target_status == ConfigStatus.ACTIVE:
        errors = _check_testing_to_active(simulation_results)
        if errors:
            raise InvalidTransitionError(
                f"Gate check failed for testing→active: {'; '.join(errors)}"
            )

    # Audit warning for active → deprecated (no hard block)
    if current_status == ConfigStatus.ACTIVE and target_status == ConfigStatus.DEPRECATED:
        logger.warning(
            "config_deprecation",
            extra={"from": current_status.value, "to": target_status.value},
        )


def validate_rollback(current_status: ConfigStatus) -> None:
    """
    Assert that rollback (→ draft) is permitted from *current_status*.

    Raises:
        InvalidTransitionError: rollback not allowed from this state.
    """
    if current_status not in ROLLBACK_ALLOWED_FROM:
        raise InvalidTransitionError(
            f"Rollback is not permitted from {current_status.value!r}. "
            f"Allowed states: {[s.value for s in ROLLBACK_ALLOWED_FROM]}"
        )
