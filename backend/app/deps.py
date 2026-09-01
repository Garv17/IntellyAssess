from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache
from app.config import settings
from app.db import get_db
from app.logging_config import bind_request_context, get_logger
from app.models import Admin, AttemptStatus, Exam, ExamAttempt, Student
from app.security import decode_token
from app.seb import verify_seb_request

log = get_logger(__name__)

bearer = HTTPBearer(auto_error=False)

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or expired credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass
class TokenData:
    subject: str
    role: str
    jti: str
    attempt_id: str | None


async def get_token_data(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> TokenData:
    # Every rejection below logs a distinct `reason`. The client always gets the
    # same opaque 401 — that's deliberate — so the log is the only place the
    # difference between "expired", "revoked" and "forged" is recorded. The
    # token itself is never logged, only its jti, which is a random opaque id.
    if creds is None:
        log.warning("auth rejected", extra={"reason": "no_bearer_credentials"})
        raise CREDENTIALS_ERROR
    try:
        payload = decode_token(creds.credentials)
    except jwt.ExpiredSignatureError:
        log.info("auth rejected", extra={"reason": "token_expired"})
        raise CREDENTIALS_ERROR from None
    except jwt.PyJWTError as exc:
        log.warning(
            "auth rejected",
            extra={"reason": "token_invalid", "error_type": type(exc).__name__},
        )
        raise CREDENTIALS_ERROR from None
    if payload.get("type") != "access":
        log.warning(
            "auth rejected",
            extra={"reason": "wrong_token_type", "token_type": payload.get("type")},
        )
        raise CREDENTIALS_ERROR
    jti = payload.get("jti", "")
    if await cache.is_jti_denied(jti):
        log.warning("auth rejected", extra={"reason": "token_revoked", "jti": jti})
        raise CREDENTIALS_ERROR

    # Stamp the actor onto the correlation context so every subsequent line this
    # request emits identifies who made it, without each call site passing it.
    bind_request_context(actor=f"{payload.get('role', '?')}:{payload['sub']}")
    return TokenData(
        subject=payload["sub"],
        role=payload.get("role", ""),
        jti=jti,
        attempt_id=payload.get("attempt_id"),
    )


async def current_student(
    token: Annotated[TokenData, Depends(get_token_data)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Student:
    if token.role != "student":
        log.warning(
            "authorization denied",
            extra={"reason": "student_role_required", "actual_role": token.role},
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Student access required")
    student = await db.get(Student, uuid.UUID(token.subject))
    if student is None:
        # A validly-signed token for a student row that no longer exists.
        log.warning("auth rejected", extra={"reason": "student_not_found"})
        raise CREDENTIALS_ERROR
    if not student.is_active:
        log.warning("auth rejected", extra={"reason": "student_inactive"})
        raise CREDENTIALS_ERROR
    return student


async def current_admin(
    token: Annotated[TokenData, Depends(get_token_data)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Admin:
    if token.role != "admin":
        # A student token presented to an admin route is the shape a privilege
        # escalation attempt takes, so this one is worth alerting on.
        log.warning(
            "authorization denied",
            extra={"reason": "admin_role_required", "actual_role": token.role},
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    admin = await db.get(Admin, uuid.UUID(token.subject))
    if admin is None:
        log.warning("auth rejected", extra={"reason": "admin_not_found"})
        raise CREDENTIALS_ERROR
    if not admin.is_active:
        log.warning("auth rejected", extra={"reason": "admin_inactive"})
        raise CREDENTIALS_ERROR
    return admin


async def active_attempt(
    student: Annotated[Student, Depends(current_student)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ExamAttempt:
    """The student's in-progress attempt. 409 if there isn't one."""
    result = await db.execute(
        select(ExamAttempt)
        .where(
            ExamAttempt.student_id == student.id,
            ExamAttempt.status == AttemptStatus.in_progress,
        )
        .order_by(ExamAttempt.started_at.desc())
        .limit(1)
    )
    attempt = result.scalar_one_or_none()
    if attempt is None:
        log.info(
            "no in-progress attempt for student",
            extra={"student_id": str(student.id)},
        )
        raise HTTPException(status.HTTP_409_CONFLICT, "No exam in progress")
    return attempt


async def attempt_exam(
    attempt: Annotated[ExamAttempt, Depends(active_attempt)],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
) -> ExamAttempt:
    """Same as active_attempt, plus the SEB Config Key check when the exam requires it."""
    exam = await db.get(Exam, attempt.exam_id)
    if exam and exam.requires_seb:
        verify_seb_request(request, exam.seb_config_key)
    return attempt


async def resolve_deadline(attempt: ExamAttempt) -> datetime:
    """Redis is the fast path; the attempt row is the source of truth if Redis is cold."""
    deadline = await cache.get_deadline(str(attempt.id))
    if deadline is None:
        # Cold Redis on the exam-critical path. Correct, but slower and worth
        # noticing if it starts happening for every request.
        log.info(
            "deadline cache miss; falling back to attempt row",
            extra={"attempt_id": str(attempt.id)},
        )
        deadline = attempt.deadline_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        await cache.set_deadline(
            str(attempt.id), deadline, cache.seconds_until(deadline) + 300
        )
    return deadline


async def writable_attempt(
    attempt: Annotated[ExamAttempt, Depends(attempt_exam)],
) -> ExamAttempt:
    """Rejects writes past the deadline. This is what makes client clock tampering moot."""
    deadline = await resolve_deadline(attempt)
    if datetime.now(UTC) > deadline.replace(tzinfo=deadline.tzinfo or UTC):
        remaining = (datetime.now(UTC) - deadline).total_seconds()
        if remaining > settings.deadline_grace_seconds:
            log.info(
                "write rejected past deadline",
                extra={
                    "attempt_id": str(attempt.id),
                    "seconds_past_deadline": round(remaining, 1),
                },
            )
            raise HTTPException(
                status.HTTP_410_GONE, "Exam time has expired; your answers were submitted"
            )
    return attempt
