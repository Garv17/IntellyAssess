"""Bulk magic-link sends. Runs on the default queue — this is outbound HTTP to
Brevo, not CPU work, so it runs on the default queue."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import settings
from app.models import ExamAttemptRecovery, Student
from app.security import create_magic_token
from app.services.email import (
    send_invite_email_sync,
    send_magic_link_email_sync,
    send_recovery_link_email_sync,
)
from app.sync_db import session_scope
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="mailer.send_bulk_magic_links")
def send_bulk_magic_links(student_ids: list[str]) -> dict:
    """Admin-triggered batch send. One failed email never aborts the rest — the
    audit log below is where an admin checks who actually got sent to."""
    sent, failed = 0, 0
    with session_scope() as session:
        students = session.execute(
            select(Student).where(Student.id.in_(uuid.UUID(sid) for sid in student_ids))
        ).scalars()

        for student in students:
            if not student.is_active:
                continue
            token, _ = create_magic_token(str(student.id))
            link = f"{settings.frontend_base_url}/auth/magic?token={token}"
            try:
                send_magic_link_email_sync(student.email, student.name, link)
                sent += 1
            except Exception:
                log.exception("bulk magic-link send failed for student %s", student.id)
                failed += 1

    return {"sent": sent, "failed": failed, "requested": len(student_ids)}


@celery_app.task(name="mailer.send_bulk_invites")
def send_bulk_invites(items: list[dict]) -> dict:
    """items: [{"email", "name", "exam_title", "token"}, ...] — the raw invite
    tokens exist only transiently in this task's payload (Redis-backed broker);
    the exam_invites table only ever stores their hash, so this queue message is
    the one place a raw token is ever seen outside the admin request/response."""
    sent, failed = 0, 0
    for item in items:
        link = f"{settings.frontend_base_url}/invite?token={item['token']}"
        try:
            send_invite_email_sync(item["email"], item["name"], item["exam_title"], link)
            sent += 1
        except Exception:
            log.exception("bulk invite send failed for %s", item["email"])
            failed += 1
    return {"sent": sent, "failed": failed, "requested": len(items)}


@celery_app.task(name="mailer.send_bulk_recovery_links")
def send_bulk_recovery_links(items: list[dict]) -> dict:
    """items: [{"recovery_id", "email", "name", "token"}, ...] — the raw token
    exists only transiently in this task's payload; exam_attempt_recoveries only
    ever stores its hash (see app/services/recovery.py). Marks each recovery
    record's sent_at only on a successful send, so a failed send is visibly
    still "pending" to the admin, not silently marked sent."""
    sent, failed = 0, 0
    with session_scope() as session:
        for item in items:
            link = f"{settings.frontend_base_url}/resume/{item['token']}"
            try:
                send_recovery_link_email_sync(item["email"], item["name"], link)
            except Exception:
                log.exception("bulk recovery-link send failed for %s", item["email"])
                failed += 1
                continue
            sent += 1
            record = session.get(ExamAttemptRecovery, uuid.UUID(item["recovery_id"]))
            if record is not None:
                record.sent_at = datetime.now(UTC)
                record.status = "sent"
    return {"sent": sent, "failed": failed, "requested": len(items)}
