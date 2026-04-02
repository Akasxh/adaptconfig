"""Comprehensive tests for the integration lifecycle state machine."""

import pytest

from finspark.schemas.common import ConfigStatus
from finspark.services.lifecycle import (
    AuditEntry,
    IntegrationLifecycle,
    InvalidTransitionError,
    TRANSITIONS,
)


class TestLifecycleTransitions:
    def test_initial_state_is_draft(self) -> None:
        lc = IntegrationLifecycle()
        assert lc.state == ConfigStatus.DRAFT

    def test_valid_draft_to_configured(self) -> None:
        lc = IntegrationLifecycle()
        entry = lc.transition(ConfigStatus.CONFIGURED)
        assert lc.state == ConfigStatus.CONFIGURED
        assert entry.from_state == ConfigStatus.DRAFT
        assert entry.to_state == ConfigStatus.CONFIGURED

    def test_valid_configured_to_validating(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        assert lc.state == ConfigStatus.VALIDATING

    def test_valid_validating_to_testing(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        assert lc.state == ConfigStatus.TESTING

    def test_valid_testing_to_active(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        assert lc.state == ConfigStatus.ACTIVE

    def test_valid_active_to_deprecated(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        lc.transition(ConfigStatus.DEPRECATED)
        assert lc.state == ConfigStatus.DEPRECATED

    def test_full_lifecycle_happy_path(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED, actor="system")
        lc.transition(ConfigStatus.VALIDATING, actor="validator")
        lc.transition(ConfigStatus.TESTING, actor="tester")
        lc.transition(ConfigStatus.ACTIVE, actor="deployer")
        lc.transition(ConfigStatus.DEPRECATED, actor="admin", reason="EOL")
        assert lc.state == ConfigStatus.DEPRECATED
        assert len(lc.audit_trail) == 5

    def test_invalid_draft_to_active(self) -> None:
        lc = IntegrationLifecycle()
        with pytest.raises(InvalidTransitionError) as exc_info:
            lc.transition(ConfigStatus.ACTIVE)
        assert "draft" in str(exc_info.value).lower()
        assert "active" in str(exc_info.value).lower()

    def test_invalid_draft_to_testing(self) -> None:
        lc = IntegrationLifecycle()
        with pytest.raises(InvalidTransitionError):
            lc.transition(ConfigStatus.TESTING)

    def test_invalid_active_to_configured(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        with pytest.raises(InvalidTransitionError):
            lc.transition(ConfigStatus.CONFIGURED)

    def test_rollback_from_active(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        lc.transition(ConfigStatus.ROLLBACK)
        assert lc.state == ConfigStatus.ROLLBACK

    def test_rollback_to_configured(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        lc.transition(ConfigStatus.ROLLBACK)
        lc.transition(ConfigStatus.CONFIGURED)
        assert lc.state == ConfigStatus.CONFIGURED

    def test_configured_back_to_draft(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.DRAFT)
        assert lc.state == ConfigStatus.DRAFT

    def test_deprecated_back_to_draft(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        lc.transition(ConfigStatus.DEPRECATED)
        lc.transition(ConfigStatus.DRAFT)
        assert lc.state == ConfigStatus.DRAFT


class TestLifecycleCanTransition:
    def test_can_transition_valid(self) -> None:
        lc = IntegrationLifecycle()
        assert lc.can_transition(ConfigStatus.CONFIGURED) is True

    def test_can_transition_invalid(self) -> None:
        lc = IntegrationLifecycle()
        assert lc.can_transition(ConfigStatus.ACTIVE) is False

    def test_get_available_transitions_draft(self) -> None:
        lc = IntegrationLifecycle()
        available = lc.get_available_transitions()
        assert ConfigStatus.CONFIGURED in available
        assert len(available) == 1

    def test_get_available_transitions_active(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        lc.transition(ConfigStatus.TESTING)
        lc.transition(ConfigStatus.ACTIVE)
        available = lc.get_available_transitions()
        assert ConfigStatus.DEPRECATED in available
        assert ConfigStatus.ROLLBACK in available


class TestAuditTrail:
    def test_audit_entry_created(self) -> None:
        lc = IntegrationLifecycle()
        entry = lc.transition(ConfigStatus.CONFIGURED, actor="admin", reason="Initial setup")
        assert isinstance(entry, AuditEntry)
        assert entry.actor == "admin"
        assert entry.reason == "Initial setup"
        assert entry.timestamp is not None

    def test_audit_trail_grows(self) -> None:
        lc = IntegrationLifecycle()
        lc.transition(ConfigStatus.CONFIGURED)
        lc.transition(ConfigStatus.VALIDATING)
        assert len(lc.audit_trail) == 2

    def test_failed_transition_no_audit_entry(self) -> None:
        lc = IntegrationLifecycle()
        with pytest.raises(InvalidTransitionError):
            lc.transition(ConfigStatus.ACTIVE)
        assert len(lc.audit_trail) == 0


class TestTransitionsMap:
    def test_all_states_have_transitions(self) -> None:
        for status in ConfigStatus:
            assert status in TRANSITIONS, f"{status} missing from TRANSITIONS"

    def test_no_self_transitions(self) -> None:
        for state, targets in TRANSITIONS.items():
            assert state not in targets, f"{state} has self-transition"


class TestInvalidTransitionError:
    def test_error_message(self) -> None:
        err = InvalidTransitionError(ConfigStatus.DRAFT, ConfigStatus.ACTIVE)
        assert "draft" in str(err).lower()
        assert "active" in str(err).lower()
        assert err.current == ConfigStatus.DRAFT
        assert err.target == ConfigStatus.ACTIVE
