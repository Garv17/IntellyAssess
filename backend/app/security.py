from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from app.config import settings

Role = Literal["student", "admin"]

BCRYPT_ROUNDS = 12
# bcrypt silently ignores bytes past 72; truncate explicitly so a long password can
# never be mistaken for a stronger one than it is.
_MAX_PASSWORD_BYTES = 72


def _encode_password(raw: str) -> bytes:
    return raw.encode("utf-8")[:_MAX_PASSWORD_BYTES]


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(_encode_password(raw), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_encode_password(raw), hashed.encode())
    except (ValueError, TypeError):
        # Malformed stored hash — treat as a failed login rather than a 500.
        return False


def _encode(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(
    subject: str,
    role: Role,
    attempt_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Returns (token, jti). attempt_id is embedded so the hot auto-save path needs no DB hit."""
    now = datetime.now(UTC)
    jti = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": "access",
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    if attempt_id:
        payload["attempt_id"] = attempt_id
    if extra:
        payload.update(extra)
    return _encode(payload), jti


def create_magic_token(subject: str) -> tuple[str, str]:
    """Short-lived, single-use-by-convention token emailed as the sign-in link.
    One-time use is enforced by the caller denylisting the jti on redemption."""
    now = datetime.now(UTC)
    jti = str(uuid.uuid4())
    payload = {
        "sub": subject,
        "type": "magic",
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(minutes=settings.magic_link_ttl_minutes),
    }
    return _encode(payload), jti


def create_refresh_token(subject: str, role: Role) -> tuple[str, str]:
    now = datetime.now(UTC)
    jti = str(uuid.uuid4())
    payload = {
        "sub": subject,
        "role": role,
        "type": "refresh",
        "jti": jti,
        "iat": now,
        "exp": now + timedelta(days=settings.refresh_token_days),
    }
    return _encode(payload), jti


def decode_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError subclasses on invalid/expired tokens."""
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
