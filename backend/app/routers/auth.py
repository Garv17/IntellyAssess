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
from app.models import Admin, AttemptStatus, AuditLog, ExamAttempt, Student
from app.schemas import AdminLogin, MeOut, RefreshRequest, StudentLogin, TokenPair
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILED = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")


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


@router.post("/student/login", response_model=TokenPair)
async def student_login(
    payload: StudentLogin,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    result = await db.execute(select(Student).where(Student.student_id == payload.student_id))
    student = result.scalar_one_or_none()

    # Verify unconditionally-shaped: same error for unknown ID and wrong password so
    # the endpoint can't be used to enumerate valid student IDs.
    if student is None or not verify_password(payload.password, student.password_hash):
        raise LOGIN_FAILED
    if not student.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account disabled")

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
