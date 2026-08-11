"""The PIN-based entry point for exams that require SEB with more than one student
involved. See backend/SEB_INTEGRATION.md §6 for why "magic link opens inside SEB
directly" doesn't work: SEB has no hook into a normal browser, and one .seb file's
Config Key must be identical for every student taking that exam, so identity can't
be encoded into a per-student Start URL. Instead:

  1. Each student gets their own emailed link to GET /redeem — a normal webpage,
     opened in a normal browser, that shows them a short-lived PIN.
  2. Every student launches the *same* .seb file (same Config Key).
  3. SEB opens a generic, exam-scoped Start URL that just asks for a PIN.
  4. POST /pin-login exchanges that PIN for a real session, the same way the
     existing magic-link verify does.

Both endpoints are unauthenticated on purpose — they're the thing that establishes
identity in the first place.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Exam, ExamInvite, Student
from app.routers.auth import _issue_student_session
from app.schemas import InvitePinLogin, InviteRedeemOut, TokenPair

router = APIRouter(prefix="/api/invite", tags=["invite"])

PIN_TTL_MINUTES = 10


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@router.get("/redeem", response_model=InviteRedeemOut)
async def redeem_invite(
    token: str, db: Annotated[AsyncSession, Depends(get_db)]
) -> InviteRedeemOut:
    """Idempotent by design: corporate mail scanners (Proofpoint, Safe Links) visit
    every link in an email automatically before a human clicks it. Every GET here
    mints a fresh PIN and invalidates whichever one existed before — that's safe
    because nothing is consumed by *viewing* a PIN, only by using it at
    /pin-login, so whichever visit happens last (almost always the real student)
    is simply the one whose PIN is authoritative."""
    result = await db.execute(select(ExamInvite).where(ExamInvite.token_hash == _hash(token)))
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This invite link is invalid.")

    exam = await db.get(Exam, invite.exam_id)
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This invite link is invalid.")

    pin = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.now(UTC)
    invite.pin_hash = _hash(pin)
    invite.pin_expires_at = now + timedelta(minutes=PIN_TTL_MINUTES)
    invite.pin_consumed_at = None
    await db.flush()

    return InviteRedeemOut(
        exam_id=exam.id,
        exam_title=exam.title,
        pin=pin,
        pin_expires_at=invite.pin_expires_at,
    )


@router.post("/pin-login", response_model=TokenPair)
async def pin_login(
    payload: InvitePinLogin,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TokenPair:
    """What a student actually types inside SEB's own window — this is the step
    that crosses from "outside SEB" to "inside SEB," and it crosses as digits
    typed by a human, not a URL parameter, so nothing needs to survive SEB's
    separate storage boundary."""
    result = await db.execute(
        select(ExamInvite)
        .where(
            ExamInvite.exam_id == payload.exam_id,
            ExamInvite.pin_hash == _hash(payload.pin),
            ExamInvite.pin_consumed_at.is_(None),
        )
        .limit(1)
    )
    invite = result.scalars().first()
    if invite is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect or already-used PIN.")
    if invite.pin_expires_at is None or _aware(invite.pin_expires_at) <= datetime.now(UTC):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "This PIN has expired — go back to your invite link for a new one.",
        )

    student = await db.get(Student, invite.student_id)
    if student is None or not student.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect or already-used PIN.")

    invite.pin_consumed_at = datetime.now(UTC)
    return await _issue_student_session(db, request, student)
