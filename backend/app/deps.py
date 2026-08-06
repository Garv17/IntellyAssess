from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache
from app.config import settings
from app.db import get_db
from app.models import Admin, AttemptStatus, ExamAttempt, Student
from app.security import decode_token

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
    if creds is None:
        raise CREDENTIALS_ERROR
    try:
        payload = decode_token(creds.credentials)
    except jwt.PyJWTError:
        raise CREDENTIALS_ERROR from None
    if payload.get("type") != "access":
        raise CREDENTIALS_ERROR
    jti = payload.get("jti", "")
    if await cache.is_jti_denied(jti):
        raise CREDENTIALS_ERROR
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
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Student access required")
    student = await db.get(Student, uuid.UUID(token.subject))
    if student is None or not student.is_active:
        raise CREDENTIALS_ERROR
    return student


async def current_admin(
    token: Annotated[TokenData, Depends(get_token_data)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Admin:
    if token.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    admin = await db.get(Admin, uuid.UUID(token.subject))
    if admin is None or not admin.is_active:
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
        raise HTTPException(status.HTTP_409_CONFLICT, "No exam in progress")
    return attempt


async def resolve_deadline(attempt: ExamAttempt) -> datetime:
    """Redis is the fast path; the attempt row is the source of truth if Redis is cold."""
    deadline = await cache.get_deadline(str(attempt.id))
    if deadline is None:
        deadline = attempt.deadline_at
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        await cache.set_deadline(
            str(attempt.id), deadline, cache.seconds_until(deadline) + 300
        )
    return deadline


async def writable_attempt(
    attempt: Annotated[ExamAttempt, Depends(active_attempt)],
) -> ExamAttempt:
    """Rejects writes past the deadline. This is what makes client clock tampering moot."""
    deadline = await resolve_deadline(attempt)
    if datetime.now(UTC) > deadline.replace(tzinfo=deadline.tzinfo or UTC):
        remaining = (datetime.now(UTC) - deadline).total_seconds()
        if remaining > settings.deadline_grace_seconds:
            raise HTTPException(
                status.HTTP_410_GONE, "Exam time has expired; your answers were submitted"
            )
    return attempt
