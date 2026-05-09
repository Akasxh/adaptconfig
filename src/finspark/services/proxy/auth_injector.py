"""Auth header injection for proxied requests."""

import base64

from finspark.core.security import decrypt_value


def _maybe_decrypt(value: str) -> str:
    if not value or value.startswith("env:"):
        return value
    try:
        return decrypt_value(value)
    except Exception:
        return value


class AuthInjector:
    def inject(self, headers: dict, auth_config: dict) -> dict:
        auth_type = auth_config.get("type", "")
        credentials = auth_config.get("credentials", {})
        result = dict(headers)

        if auth_type == "bearer":
            token = _maybe_decrypt(credentials.get("token", ""))
            result["Authorization"] = f"Bearer {token}"

        elif auth_type == "api_key":
            header_name = auth_config.get("header", "X-API-Key")
            key = _maybe_decrypt(credentials.get("api_key", ""))
            result[header_name] = key

        elif auth_type == "basic":
            username = _maybe_decrypt(credentials.get("username", ""))
            password = _maybe_decrypt(credentials.get("password", ""))
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
            result["Authorization"] = f"Basic {encoded}"

        elif auth_type == "oauth2":
            token = _maybe_decrypt(credentials.get("token", ""))
            result["Authorization"] = f"Bearer {token}"

        return result
