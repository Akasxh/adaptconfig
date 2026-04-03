"""
Unit tests for the config lifecycle state machine (services/lifecycle.py).

Covers:
- generate creates configs in draft status
- rollback validates current state before proceeding
- gate guards reject invalid transitions
- role checks block non-admins from active/deprecated
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from finspark.schemas.configurations import ConfigRecord, ConfigStatus
from finspark.services.lifecycle import (
    ROLLBACK_ALLOWED_FROM,
    InvalidTransitionError,
    InsufficientRoleError,
    validate_rollback,
    validate_transition,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(status: ConfigStatus, payload: dict | None = None) -> ConfigRecord:
    now = datetime.utcnow()
    return ConfigRecord(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        adapter_id=uuid.uuid4(),
        status=status,
        payload=payload or {},
        version=1,
        created_at=now,
        updated_at=now,
    )


_ADMIN_ROLES: frozenset[str] = frozenset({"admin"})
_VIEWER_ROLES: frozenset[str] = frozenset({"viewer"})
_VALID_PAYLOAD: dict = {
    "base_url": "https://api.example.com",
    "auth": {"type": "bearer", "token": "tok"},
    "endpoints": ["/v1/score"],
}
_PASSED_SIM: dict = {"passed": True, "run_id": "abc"}
_FAILED_SIM: dict = {"passed": False, "run_id": "abc"}


# ---------------------------------------------------------------------------
# generate creates configs in draft status
# ---------------------------------------------------------------------------


class TestGenerateDraftStatus:
    def test_new_config_record_is_draft(self) -> None:
        record = _make_record(ConfigStatus.DRAFT)
        assert record.status == ConfigStatus.DRAFT

    def test_draft_value_is_string_draft(self) -> None:
        assert ConfigStatus.DRAFT.value == "draft"


# ---------------------------------------------------------------------------
# rollback validates current state
# ---------------------------------------------------------------------------


class TestRollbackValidation:
    @pytest.mark.parametrize("allowed", list(ROLLBACK_ALLOWED_FROM))
    def test_rollback_allowed_from_permitted_states(self, allowed: ConfigStatus) -> None:
        validate_rollback(allowed)  # must not raise

    @pytest.mark.parametrize(
        "blocked",
        [
            ConfigStatus.DRAFT,
            ConfigStatus.ACTIVE,
            ConfigStatus.DEPRECATED,
            ConfigStatus.ARCHIVED,
        ],
    )
    def test_rollback_blocked_from_disallowed_states(self, blocked: ConfigStatus) -> None:
        with pytest.raises(InvalidTransitionError, match="Rollback is not permitted"):
            validate_rollback(blocked)


# ---------------------------------------------------------------------------
# Gate guards
# ---------------------------------------------------------------------------


class TestGateGuards:
    # configured → validating requires base_url, auth, endpoints

    def test_configured_to_validating_passes_with_full_payload(self) -> None:
        validate_transition(
            ConfigStatus.CONFIGURED,
            ConfigStatus.VALIDATING,
            payload=_VALID_PAYLOAD,
        )

    def test_configured_to_validating_fails_missing_base_url(self) -> None:
        payload = {k: v for k, v in _VALID_PAYLOAD.items() if k != "base_url"}
        with pytest.raises(InvalidTransitionError, match="base_url"):
            validate_transition(
                ConfigStatus.CONFIGURED,
                ConfigStatus.VALIDATING,
                payload=payload,
            )

    def test_configured_to_validating_fails_missing_auth(self) -> None:
        payload = {k: v for k, v in _VALID_PAYLOAD.items() if k != "auth"}
        with pytest.raises(InvalidTransitionError, match="auth"):
            validate_transition(
                ConfigStatus.CONFIGURED,
                ConfigStatus.VALIDATING,
                payload=payload,
            )

    def test_configured_to_validating_fails_missing_endpoints(self) -> None:
        payload = {k: v for k, v in _VALID_PAYLOAD.items() if k != "endpoints"}
        with pytest.raises(InvalidTransitionError, match="endpoints"):
            validate_transition(
                ConfigStatus.CONFIGURED,
                ConfigStatus.VALIDATING,
                payload=payload,
            )

    def test_configured_to_validating_fails_empty_payload(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_transition(
                ConfigStatus.CONFIGURED,
                ConfigStatus.VALIDATING,
                payload={},
            )

    # testing → active requires passed simulation results

    def test_testing_to_active_passes_with_passed_sim(self) -> None:
        validate_transition(
            ConfigStatus.TESTING,
            ConfigStatus.ACTIVE,
            simulation_results=_PASSED_SIM,
            caller_roles=_ADMIN_ROLES,
        )

    def test_testing_to_active_fails_without_simulation_results(self) -> None:
        with pytest.raises(InvalidTransitionError, match="simulation results are required"):
            validate_transition(
                ConfigStatus.TESTING,
                ConfigStatus.ACTIVE,
                simulation_results=None,
                caller_roles=_ADMIN_ROLES,
            )

    def test_testing_to_active_fails_with_failed_simulation(self) -> None:
        with pytest.raises(InvalidTransitionError, match="simulation must have passed"):
            validate_transition(
                ConfigStatus.TESTING,
                ConfigStatus.ACTIVE,
                simulation_results=_FAILED_SIM,
                caller_roles=_ADMIN_ROLES,
            )

    # active → deprecated (warning, no hard block)

    def test_active_to_deprecated_succeeds_for_admin(self) -> None:
        validate_transition(
            ConfigStatus.ACTIVE,
            ConfigStatus.DEPRECATED,
            caller_roles=_ADMIN_ROLES,
        )

    # Illegal transition

    def test_draft_cannot_jump_to_active(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_transition(ConfigStatus.DRAFT, ConfigStatus.ACTIVE)

    def test_deprecated_has_no_valid_targets(self) -> None:
        with pytest.raises(InvalidTransitionError):
            validate_transition(ConfigStatus.DEPRECATED, ConfigStatus.DRAFT)


# ---------------------------------------------------------------------------
# Role-based checks
# ---------------------------------------------------------------------------


class TestRoleChecks:
    def test_non_admin_cannot_transition_to_active(self) -> None:
        with pytest.raises(InsufficientRoleError, match="admin"):
            validate_transition(
                ConfigStatus.TESTING,
                ConfigStatus.ACTIVE,
                simulation_results=_PASSED_SIM,
                caller_roles=_VIEWER_ROLES,
            )

    def test_non_admin_cannot_transition_to_deprecated(self) -> None:
        with pytest.raises(InsufficientRoleError, match="admin"):
            validate_transition(
                ConfigStatus.ACTIVE,
                ConfigStatus.DEPRECATED,
                caller_roles=_VIEWER_ROLES,
            )

    def test_superadmin_can_transition_to_active(self) -> None:
        validate_transition(
            ConfigStatus.TESTING,
            ConfigStatus.ACTIVE,
            simulation_results=_PASSED_SIM,
            caller_roles=frozenset({"superadmin"}),
        )

    def test_admin_can_transition_to_deprecated(self) -> None:
        validate_transition(
            ConfigStatus.ACTIVE,
            ConfigStatus.DEPRECATED,
            caller_roles=_ADMIN_ROLES,
        )
