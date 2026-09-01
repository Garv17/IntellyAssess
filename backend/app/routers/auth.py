from __future__ import annotations

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
from app.logging_config import get_logger, mask_email
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

log = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILED = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
# Same response whether the email is unregistered, inactive, or rate-limited — an
# attacker learns nothing, and a real student always sees the same reassuring text.
MAGIC_LINK_ACK = MagicLinkSent()


async def _log(db: AsyncSession, actor_type: str, actor_id: uuid.UUID | None, action: str, **meta):
    db.add(AuditLog(actor_type=actor_type, actor_id=actor_id, action=action, meta=meta))


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


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
        log.info(
            "student session replaced by newer login",
            extra={"student_id": str(student.id), "client_ip": _client_ip(request)},
        )

    await _log(db, "student", student.id, "login", ip=request.client.host if request.client else None)
    log.info(
        "student login succeeded",
        extra={
            "student_id": str(student.id),
            "attempt_id": str(attempt_id) if attempt_id else None,
            "client_ip": _client_ip(request),
        },
    )
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
    # The caller cannot tell these branches apart — by design — so the log is
    # the only way an operator can answer "why did this student never get a
    # link". The address is masked: it is PII and the student ID identifies the
    # row precisely anyway.
    if student is None:
        log.info(
            "magic link not sent",
            extra={"reason": "unknown_email", "email_masked": mask_email(payload.email)},
        )
        return MAGIC_LINK_ACK
    if not student.is_active:
        log.info(
            "magic link not sent",
            extra={"reason": "student_inactive", "student_id": str(student.id)},
        )
        return MAGIC_LINK_ACK

    allowed = await cache.start_magic_cooldown(
        str(student.id), settings.magic_link_cooldown_seconds
    )
    if not allowed:
        log.info(
            "magic link not sent",
            extra={"reason": "cooldown_active", "student_id": str(student.id)},
        )
        return MAGIC_LINK_ACK

    token, _ = create_magic_token(str(student.id))
    link = f"{settings.frontend_base_url}/auth/magic?token={token}"
    try:
        await send_magic_link_email(student.email, student.name, link)
    except Exception:
        # The link itself is a bearer credential and is never logged.
        log.exception(
            "magic link email send failed",
            extra={"student_id": str(student.id)},
        )
        if settings.environment != "development":
            return MAGIC_LINK_ACK
    else:
        log.info("magic link sent", extra={"student_id": str(student.id)})

    await _log(db, "student", student.id, "magic_link_requested")
    # Dev-only: hands the raw token back so a local sign-in works without a real
    # mailbox (see SETUP.md). Never populated outside ENVIRONMENT=development.
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
    except jwt.PyJWTError as exc:
        log.warning(
            "magic link verification failed",
            extra={"reason": "token_invalid", "error_type": type(exc).__name__},
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired link") from None
    if claims.get("type") != "magic":
        log.warning("magic link verification failed", extra={"reason": "wrong_token_type"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired link")

    jti = claims.get("jti", "")
    if await cache.is_jti_denied(jti):
        # Routine: mail scanners pre-visit links. Not a security event.
        log.info("magic link verification failed", extra={"reason": "already_used", "jti": jti})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This link has already been used")
    # Deny immediately so a second redemption (e.g. an email link-preview crawler) fails.
    await cache.deny_jti(jti, settings.magic_link_ttl_minutes * 60)

    student = await db.get(Student, uuid.UUID(claims["sub"]))
    if student is None or not student.is_active:
        log.warning(
            "magic link verification failed",
            extra={
                "reason": "student_missing_or_inactive",
                "student_id": claims.get("sub"),
            },
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired link")

    log.info("magic link redeemed", extra={"student_id": str(student.id)})
    return await _issue_student_session(db, request, student)


@router.post("/admin/login", response_model=TokenPair)
async def admin_login(
    payload: AdminLogin,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    result = await db.execute(select(Admin).where(Admin.email == payload.email.lower()))
    admin = result.scalar_one_or_none()
    # Failed admin logins had no record at all before this — neither a log line
    # nor an audit row — so a password-guessing run against the admin console
    # was entirely invisible. The submitted password is of course never logged,
    # and the address is masked.
    if admin is None:
        log.warning(
            "admin login failed",
            extra={
                "reason": "unknown_email",
                "email_masked": mask_email(payload.email),
                "client_ip": _client_ip(request),
            },
        )
        raise LOGIN_FAILED
    if not verify_password(payload.password, admin.password_hash):
        log.warning(
            "admin login failed",
            extra={
                "reason": "bad_password",
                "admin_id": str(admin.id),
                "client_ip": _client_ip(request),
            },
        )
        raise LOGIN_FAILED
    if not admin.is_active:
        log.warning(
            "admin login failed",
            extra={
                "reason": "account_disabled",
                "admin_id": str(admin.id),
                "client_ip": _client_ip(request),
            },
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")
    pair, _ = _token_pair(str(admin.id), "admin")
    await _log(db, "admin", admin.id, "login")
    log.info(
        "admin login succeeded",
        extra={"admin_id": str(admin.id), "client_ip": _client_ip(request)},
    )
    return pair


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    payload: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    try:
        claims = decode_token(payload.refresh_token)
    except jwt.PyJWTError as exc:
        log.warning(
            "token refresh failed",
            extra={"reason": "token_invalid", "error_type": type(exc).__name__},
        )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token") from None
    if claims.get("type") != "refresh":
        log.warning("token refresh failed", extra={"reason": "wrong_token_type"})
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if await cache.is_jti_denied(claims.get("jti", "")):
        log.warning(
            "token refresh failed",
            extra={"reason": "token_revoked", "subject": claims.get("sub")},
        )
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
    log.info("logout", extra={"role": token.role, "subject": token.subject})


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
