"""Tests for config.py production validation to boost coverage."""

import pytest
from pydantic import ValidationError

from finspark.core.config import Settings, _is_insecure


class TestIsInsecure:
    def test_insecure_patterns(self) -> None:
        assert _is_insecure("change-me-please") is True
        assert _is_insecure("CHANGE-ME-PLEASE") is True
        assert _is_insecure("this-is-insecure") is True

    def test_secure_values(self) -> None:
        assert _is_insecure("a" * 64) is False
        assert _is_insecure("super-strong-production-secret-key-here") is False


class TestSettingsValidation:
    def test_debug_mode_skips_validation(self) -> None:
        s = Settings(debug=True)
        assert s.debug is True

    def test_production_rejects_insecure_secret_key(self) -> None:
        with pytest.raises(ValidationError, match="insecure default"):
            Settings(
                debug=False,
                secret_key="change-me-in-production-use-openssl-rand-hex-32",
                encryption_key="a" * 64,
            )

    def test_production_rejects_short_key(self) -> None:
        with pytest.raises(ValidationError, match="at least 32"):
            Settings(
                debug=False,
                secret_key="short",
                encryption_key="a" * 64,
            )

    def test_production_rejects_insecure_encryption_key(self) -> None:
        with pytest.raises(ValidationError, match="insecure default"):
            Settings(
                debug=False,
                secret_key="a" * 64,
                encryption_key="change-me-in-production",
            )

    def test_production_accepts_strong_keys(self) -> None:
        s = Settings(
            debug=False,
            secret_key="a" * 64,
            encryption_key="b" * 64,
        )
        assert s.debug is False

    def test_app_name_is_adaptconfig(self) -> None:
        s = Settings(debug=True)
        assert s.app_name == "AdaptConfig Integration Engine"
