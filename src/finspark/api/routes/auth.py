"""Authentication routes — register, login, refresh, me."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finspark.core.database import get_db
from finspark.core.security import create_jwt_token, decode_jwt_token
from finspark.models.user import User

router = APIRouter(prefix="/auth", tags=["Authentication"])

# ── Password hashing (PBKDF2-HMAC-SHA256 via stdlib) ─────────────────────────

_ITERATIONS = 260_000
_HASH_NAME = "sha256"


def _hash_password(password: str) -> str:
    salt = os.urandom(32)
    dk = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode(), salt, _ITERATIONS)
    return salt.hex() + "$" + dk.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$", 1)
        salt = bytes.fromhex(salt_hex)
        dk_expected = bytes.fromhex(dk_hex)
        dk_actual = hashlib.pbkdf2_hmac(_HASH_NAME, password.encode(), salt, _ITERATIONS)
        return hmac.compare_digest(dk_actual, dk_expected)
    except Exception:
        return False


# ── Schemas ───────────────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    tenant_id: str

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_tokens(user: User) -> tuple[str, str]:
    """Return (access_token, refresh_token) for the given user."""
    payload_base: dict[str, str] = {
        "sub": user.id,
        "email": user.email,
        "role": user.role,
        "tenant_id": user.tenant_id,
        "tenant_name": user.name,
    }
    access_token = create_jwt_token(
        {**payload_base, "type": "access"},
        expires_delta=timedelta(minutes=30),
    )
    refresh_token = create_jwt_token(
        {**payload_base, "type": "refresh"},
        expires_delta=timedelta(days=7),
    )
    return access_token, refresh_token


# ── Routes ────────────────────────────────────────────────────────────────────


@router.post("/register", response_model=UserOut, status_code=201)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)) -> UserOut:
    """Register a new user. Returns user info (no token) so the client can login."""
    result = await db.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    if len(body.password) < 8:
        raise HTTPException(status_code=422, detail="Password must be at least 8 characters")

    from uuid import uuid4
    user_id = str(uuid4())
    user = User(
        id=user_id,
        email=body.email,
        name=body.name,
        password_hash=_hash_password(body.password),
        tenant_id=user_id,  # Each user gets their own tenant for data isolation
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    """Authenticate with email + password. Returns access + refresh tokens."""
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if not user or not _verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    access_token, refresh_token = _make_tokens(user)
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut.model_validate(user),
    )


@router.post("/refresh")
async def refresh_token(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> dict:
    """Exchange a valid refresh token for a new access token."""
    import jwt as pyjwt

    try:
        payload = decode_jwt_token(body.refresh_token)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    from datetime import timedelta

    access_token = create_jwt_token(
        {
            "sub": user.id,
            "email": user.email,
            "role": user.role,
            "tenant_id": user.tenant_id,
            "tenant_name": user.name,
            "type": "access",
        },
        expires_delta=timedelta(minutes=30),
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserOut)
async def me(request: Request, db: AsyncSession = Depends(get_db)) -> UserOut:
    """Return the current authenticated user's profile."""
    import jwt as pyjwt

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = auth_header[len("Bearer "):]
    try:
        payload = decode_jwt_token(token)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut.model_validate(user)
