from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache
from app.config import settings
from app.db import get_db
from app.deps import TokenData, get_token_data
from app.models import Admin, AttemptStatus, AuditLog, ExamAttempt, Student
from app.schemas import (
    AdminLogin,
    MagicLinkRequest,
    MagicLinkSent,
    MagicLinkVerify,
    MeOut,
    RefreshRequest,
    TokenPair,
)
from app.security import (
    create_access_token,
    create_magic_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.services.email import send_magic_link_email

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILED = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
# Same response whether the email is unregistered, inactive, or rate-limited — an
# attacker learns nothing, and a real student always sees the same reassuring text.
MAGIC_LINK_ACK = MagicLinkSent()


async def _log(db: AsyncSession, actor_type: str, actor_id: uuid.UUID | None, action: str, **meta):
    db.add(AuditLog(actor_type=actor_type, actor_id=actor_id, action=action, meta=meta))


def _token_pair(subject: str, role, attempt_id: str | None = None) -> tuple[TokenPair, str]:
    access, jti = create_access_token(subject, role, attempt_id=attempt_id)
    refresh, _ = create_refresh_token(subject, role)
    pair = TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_minutes * 60,
    )
    return pair, jti


async def _issue_student_session(
    db: AsyncSession, request: Request, student: Student
) -> TokenPair:
    # Bind an in-progress attempt into the token so the auto-save path skips a DB lookup.
    attempt_result = await db.execute(
        select(ExamAttempt.id)
        .where(
            ExamAttempt.student_id == student.id,
            ExamAttempt.status == AttemptStatus.in_progress,
        )
        .limit(1)
    )
    attempt_id = attempt_result.scalar_one_or_none()

    pair, jti = _token_pair(str(student.id), "student", str(attempt_id) if attempt_id else None)

    # One active session per student ID: a second login revokes the first.
    previous = await cache.register_session(
        str(student.id), jti, settings.refresh_token_days * 86400
    )
    if previous and previous != jti:
        await cache.deny_jti(previous, settings.access_token_minutes * 60)
        await _log(
            db, "student", student.id, "session_replaced", ip=request.client.host if request.client else None
        )

    await _log(db, "student", student.id, "login", ip=request.client.host if request.client else None)
    return pair


@router.post("/student/magic-link/request", response_model=MagicLinkSent)
async def request_magic_link(
    payload: MagicLinkRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MagicLinkSent:
    result = await db.execute(select(Student).where(Student.email == payload.email.lower()))
    student = result.scalar_one_or_none()

    # Always return the same ack — unknown email, disabled account, cooldown, and a
    # send failure are indistinguishable to the caller, same principle as password
    # login's unconditionally-shaped error.
    if student is None or not student.is_active:
        return MAGIC_LINK_ACK

    allowed = await cache.start_magic_cooldown(
        str(student.id), settings.magic_link_cooldown_seconds
    )
    if not allowed:
        return MAGIC_LINK_ACK

    token, _ = create_magic_token(str(student.id))
    link = f"{settings.frontend_base_url}/auth/magic?token={token}"
    # dev_token below already hands the raw token back, so there's nothing for a real
    # email to accomplish in development — skip the outbound Brevo call entirely rather
    # than pay for a real HTTPS round trip per login. Under concurrent load this call
    # was the actual bottleneck: 120 simultaneous logins meant 120 real TLS handshakes
    # competing for CPU while each held a pooled DB connection open for the duration.
    if settings.environment != "development":
        try:
            await send_magic_link_email(student.email, student.name, link)
        except Exception:
            log.exception("failed to send magic link to student %s", student.id)
            return MAGIC_LINK_ACK

    await _log(db, "student", student.id, "magic_link_requested")
    # Dev-only: hands the raw token back so scripts/loadtest.py can redeem it without
    # a real mailbox. Never populated outside ENVIRONMENT=development.
    dev_token = token if settings.environment == "development" else None
    return MagicLinkSent(dev_token=dev_token)


@router.post("/student/magic-link/verify", response_model=TokenPair)
async def verify_magic_link(
    payload: MagicLinkVerify,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    try:
        claims = decode_token(payload.token)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired link") from None
    if claims.get("type") != "magic":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired link")

    jti = claims.get("jti", "")
    if await cache.is_jti_denied(jti):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This link has already been used")
    # Deny immediately so a second redemption (e.g. an email link-preview crawler) fails.
    await cache.deny_jti(jti, settings.magic_link_ttl_minutes * 60)

    student = await db.get(Student, uuid.UUID(claims["sub"]))
    if student is None or not student.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired link")

    return await _issue_student_session(db, request, student)


@router.post("/admin/login", response_model=TokenPair)
async def admin_login(
    payload: AdminLogin,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    result = await db.execute(select(Admin).where(Admin.email == payload.email.lower()))
    admin = result.scalar_one_or_none()
    if admin is None or not verify_password(payload.password, admin.password_hash):
        raise LOGIN_FAILED
    if not admin.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    pair, _ = _token_pair(str(admin.id), "admin")
    await _log(db, "admin", admin.id, "login")
    return pair


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from None
    if claims.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if await cache.is_jti_denied(claims.get("jti", "")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token revoked")

    subject, role = claims["sub"], claims.get("role")
    attempt_id = None
    if role == "student":
        result = await db.execute(
            select(ExamAttempt.id)
            .where(
                ExamAttempt.student_id == uuid.UUID(subject),
                ExamAttempt.status == AttemptStatus.in_progress,
            )
            .limit(1)
        )
        found = result.scalar_one_or_none()
        attempt_id = str(found) if found else None

    pair, _ = _token_pair(subject, role, attempt_id)
    return pair


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(token: Annotated[TokenData, Depends(get_token_data)]) -> None:
    await cache.deny_jti(token.jti, settings.access_token_minutes * 60)


@router.get("/me", response_model=MeOut)
async def me(
    token: Annotated[TokenData, Depends(get_token_data)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> MeOut:
    if token.role == "student":
        student = await db.get(Student, uuid.UUID(token.subject))
        if student is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
        return MeOut(
            id=student.id, name=student.name, role="student", identifier=student.student_id
        )
    admin = await db.get(Admin, uuid.UUID(token.subject))
    if admin is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return MeOut(id=admin.id, name=admin.name, role=admin.role, identifier=admin.email)


@router.get("/time")
async def server_time() -> dict[str, str]:
    """Clients sync their countdown against this, never the local clock."""
    return {"server_time": datetime.now(UTC).isoformat()}
