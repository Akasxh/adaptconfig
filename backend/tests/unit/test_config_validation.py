"""Tests for configuration security validation."""

from __future__ import annotations

import importlib
import importlib.util
import os
import pathlib
import sys

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BACKEND_MOD = "finspark.core.config"
_SRC_CONFIG_PATH = (
    pathlib.Path(__file__).parents[3] / "src" / "finspark" / "core" / "config.py"
)


def _import_backend_settings():
    """Import the backend Settings class with a safe environment so the
    module-level ``settings = Settings()`` call does not fail.
    """
    # Ensure module is re-imported fresh with dev env active
    saved = sys.modules.pop(_BACKEND_MOD, None)
    old_env = {k: os.environ.get(k) for k in ("APP_ENV", "APP_SECRET_KEY")}
    os.environ["APP_ENV"] = "development"
    os.environ["APP_SECRET_KEY"] = "insecure-default-change-in-production"
    try:
        mod = importlib.import_module(_BACKEND_MOD)
        return mod.Settings
    finally:
        # Restore original env
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Remove from sys.modules so later imports are fresh
        sys.modules.pop(_BACKEND_MOD, None)
        if saved is not None:
            sys.modules[_BACKEND_MOD] = saved


def _import_src_settings(module_alias: str):
    """Load src/finspark/core/config.py directly via its file path.

    The module-level ``settings = Settings()`` call uses the ``FINSPARK_``
    env prefix.  We set FINSPARK_DEBUG=true so the singleton load succeeds
    with insecure defaults, then return the bare ``Settings`` class for tests
    to construct with custom arguments.
    """
    old_debug = os.environ.get("FINSPARK_DEBUG")
    os.environ["FINSPARK_DEBUG"] = "true"
    try:
        spec = importlib.util.spec_from_file_location(module_alias, _SRC_CONFIG_PATH)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod.Settings
    finally:
        if old_debug is None:
            os.environ.pop("FINSPARK_DEBUG", None)
        else:
            os.environ["FINSPARK_DEBUG"] = old_debug


# ---------------------------------------------------------------------------
# backend/src/finspark/core/config.py — Settings
# ---------------------------------------------------------------------------


class TestBackendSettingsDefaults:
    def test_app_env_defaults_to_production(self):
        Settings = _import_backend_settings()
        assert Settings.model_fields["APP_ENV"].default == "production"

    def test_app_debug_defaults_to_false(self):
        Settings = _import_backend_settings()
        assert Settings.model_fields["APP_DEBUG"].default is False


class TestBackendSecretKeyValidator:
    """APP_SECRET_KEY is rejected for insecure values when APP_ENV != 'development'."""

    def test_insecure_key_rejected_in_production(self):
        Settings = _import_backend_settings()
        with pytest.raises(ValidationError, match="insecure"):
            Settings(
                APP_ENV="production",
                APP_SECRET_KEY="insecure-default-change-in-production",
            )

    def test_change_me_key_rejected_in_production(self):
        Settings = _import_backend_settings()
        with pytest.raises(ValidationError, match="insecure"):
            Settings(
                APP_ENV="production",
                APP_SECRET_KEY="change-me-to-something-real-later",
            )

    def test_insecure_key_rejected_in_staging(self):
        Settings = _import_backend_settings()
        with pytest.raises(ValidationError, match="insecure"):
            Settings(
                APP_ENV="staging",
                APP_SECRET_KEY="insecure-default-change-in-production",
            )

    def test_short_key_rejected_in_production(self):
        Settings = _import_backend_settings()
        with pytest.raises(ValidationError, match="32 characters"):
            Settings(
                APP_ENV="production",
                APP_SECRET_KEY="tooshort",
            )

    def test_strong_key_accepted_in_production(self):
        Settings = _import_backend_settings()
        s = Settings(
            APP_ENV="production",
            APP_SECRET_KEY="a" * 32,
        )
        assert s.APP_SECRET_KEY == "a" * 32

    def test_insecure_key_allowed_in_development(self):
        Settings = _import_backend_settings()
        s = Settings(
            APP_ENV="development",
            APP_SECRET_KEY="insecure-default-change-in-production",
        )
        assert s.APP_ENV == "development"

    def test_short_key_allowed_in_development(self):
        Settings = _import_backend_settings()
        s = Settings(
            APP_ENV="development",
            APP_SECRET_KEY="short",
        )
        assert s.APP_SECRET_KEY == "short"


# ---------------------------------------------------------------------------
# src/finspark/core/config.py — Settings (debug flag controls enforcement)
# ---------------------------------------------------------------------------


class TestSrcSettingsValidator:
    """secret_key / encryption_key are rejected when debug=False."""

    def test_insecure_secret_key_rejected_when_debug_false(self):
        Settings = _import_src_settings("_src_cfg_a")
        with pytest.raises(ValidationError, match="insecure"):
            Settings(debug=False, secret_key="change-me-in-production", encryption_key="a" * 32)

    def test_insecure_encryption_key_rejected_when_debug_false(self):
        Settings = _import_src_settings("_src_cfg_b")
        with pytest.raises(ValidationError, match="insecure"):
            Settings(debug=False, secret_key="a" * 32, encryption_key="change-me-in-production")

    def test_short_key_rejected_when_debug_false(self):
        Settings = _import_src_settings("_src_cfg_c")
        with pytest.raises(ValidationError, match="32 characters"):
            Settings(debug=False, secret_key="short", encryption_key="a" * 32)

    def test_insecure_defaults_allowed_when_debug_true(self):
        Settings = _import_src_settings("_src_cfg_d")
        s = Settings(
            debug=True,
            secret_key="change-me-in-production-use-openssl-rand-hex-32",
            encryption_key="change-me-in-production",
        )
        assert s.debug is True

    def test_strong_keys_accepted_when_debug_false(self):
        Settings = _import_src_settings("_src_cfg_e")
        s = Settings(debug=False, secret_key="a" * 32, encryption_key="b" * 32)
        assert s.debug is False
        assert s.secret_key == "a" * 32
        assert s.encryption_key == "b" * 32
