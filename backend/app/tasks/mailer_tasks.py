"""Bulk magic-link sends. Runs on the default queue — this is outbound HTTP to
Brevo, not CPU work, so it doesn't need the judge queue's isolation."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from app.config import settings
from app.models import Student
from app.security import create_magic_token
from app.services.email import send_magic_link_email_sync
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
