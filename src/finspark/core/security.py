"""Security utilities - encryption, JWT, PII masking."""

import base64
import hashlib
import re
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.fernet import Fernet

from finspark.core.config import settings


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a valid Fernet key from an arbitrary string."""
    key_bytes = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key_bytes)


_fernet = Fernet(_derive_fernet_key(settings.encryption_key))

# PII patterns
PII_PATTERNS = {
    "aadhaar": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "phone": re.compile(r"\b(?:\+91[\s-]?)?\d{10}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "account": re.compile(r"\b\d{9,18}\b"),
}


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string using Fernet symmetric encryption."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted string."""
    return _fernet.decrypt(ciphertext.encode()).decode()


def create_jwt_token(data: dict[str, str], expires_delta: timedelta | None = None) -> str:
    """Create a JWT token."""
    to_encode: dict[str, object] = dict(data)
    expire = datetime.now(UTC) + (expires_delta or timedelta(minutes=settings.jwt_expiry_minutes))
    to_encode["exp"] = int(expire.timestamp())
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_jwt_token(token: str) -> dict[str, str]:
    """Decode and validate a JWT token."""
    return jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])


def mask_pii(text: str) -> str:
    """Mask PII data in text for logging/display."""
    masked = text
    for pii_type, pattern in PII_PATTERNS.items():
        if pii_type == "aadhaar":
            masked = pattern.sub("XXXX-XXXX-XXXX", masked)
        elif pii_type == "pan":
            masked = pattern.sub("XXXXX****X", masked)
        elif pii_type == "phone":
            masked = pattern.sub("XXXXXXXXXX", masked)
        elif pii_type == "email":
            masked = pattern.sub("***@***.***", masked)
        elif pii_type == "account":
            masked = pattern.sub("XXXXXXXXXX", masked)
    return masked


def hash_value(value: str) -> str:
    """Create a SHA-256 hash of a value."""
    return hashlib.sha256(value.encode()).hexdigest()


def create_tenant_token(tenant_id: str, tenant_name: str, role: str) -> str:
    """Create a JWT containing tenant identity claims.

    Used by testing utilities and dev tooling to generate valid Bearer tokens
    for the TenantMiddleware in production mode.
    """
    payload = {
        "tenant_id": tenant_id,
        "tenant_name": tenant_name,
        "role": role,
    }
    return create_jwt_token(payload)
