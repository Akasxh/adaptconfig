"""Tests for auth header injection."""

import base64

from finspark.services.proxy.auth_injector import AuthInjector


class TestAuthInjector:
    def setup_method(self) -> None:
        self.injector = AuthInjector()

    def test_bearer_auth(self) -> None:
        headers = self.injector.inject(
            {},
            {"type": "bearer", "credentials": {"token": "my-token"}},
        )
        assert headers["Authorization"] == "Bearer my-token"

    def test_api_key_auth(self) -> None:
        headers = self.injector.inject(
            {},
            {"type": "api_key", "credentials": {"api_key": "secret-key"}},
        )
        assert headers["X-API-Key"] == "secret-key"

    def test_api_key_custom_header(self) -> None:
        headers = self.injector.inject(
            {},
            {
                "type": "api_key",
                "header": "X-Custom-Key",
                "credentials": {"api_key": "secret-key"},
            },
        )
        assert headers["X-Custom-Key"] == "secret-key"

    def test_basic_auth(self) -> None:
        headers = self.injector.inject(
            {},
            {
                "type": "basic",
                "credentials": {"username": "user", "password": "pass"},
            },
        )
        expected = base64.b64encode(b"user:pass").decode()
        assert headers["Authorization"] == f"Basic {expected}"

    def test_no_auth(self) -> None:
        headers = self.injector.inject({"X-Custom": "val"}, {})
        assert "Authorization" not in headers
        assert headers["X-Custom"] == "val"

    def test_existing_headers_preserved(self) -> None:
        headers = self.injector.inject(
            {"X-Request-ID": "123"},
            {"type": "bearer", "credentials": {"token": "tk"}},
        )
        assert headers["X-Request-ID"] == "123"
        assert headers["Authorization"] == "Bearer tk"
