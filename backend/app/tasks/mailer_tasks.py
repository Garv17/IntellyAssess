"""Bulk magic-link sends. Runs on the default queue — this is outbound HTTP to
Brevo, not CPU work, so it runs on the default queue."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.config import settings
from app.logging_config import get_logger, mask_email
from app.models import Student
from app.security import create_magic_token
from app.services.email import send_invite_email_sync, send_magic_link_email_sync
from app.sync_db import session_scope
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


@celery_app.task(name="mailer.send_bulk_magic_links")
def send_bulk_magic_links(student_ids: list[str]) -> dict:
    """Admin-triggered batch send. One failed email never aborts the rest — the
    audit log below is where an admin checks who actually got sent to."""
    sent, failed, skipped = 0, 0, 0
    with session_scope() as session:
        students = session.execute(
            select(Student).where(Student.id.in_(uuid.UUID(sid) for sid in student_ids))
        ).scalars()

        for student in students:
            if not student.is_active:
                skipped += 1
                continue
            token, _ = create_magic_token(str(student.id))
            link = f"{settings.frontend_base_url}/auth/magic?token={token}"
            try:
                send_magic_link_email_sync(student.email, student.name, link)
                sent += 1
            except Exception:
                # student_id identifies the row exactly; the address is PII and
                # the link is a bearer credential, so neither is logged.
                log.exception(
                    "bulk magic-link send failed",
                    extra={"student_id": str(student.id)},
                )
                failed += 1

    # A per-send success line would be one log per student in a 400-cohort blast.
    level_args = {
        "requested": len(student_ids),
        "sent": sent,
        "failed": failed,
        "skipped_inactive": skipped,
    }
    if failed:
        log.error("bulk magic-link send completed with failures", extra=level_args)
    else:
        log.info("bulk magic-link send completed", extra=level_args)

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
            # `item` holds the raw invite token (see the docstring above) — never
            # log the dict, and mask the address.
            log.exception(
                "bulk invite send failed",
                extra={"email_masked": mask_email(item.get("email"))},
            )
            failed += 1

    summary = {"requested": len(items), "sent": sent, "failed": failed}
    if failed:
        log.error("bulk invite send completed with failures", extra=summary)
    else:
        log.info("bulk invite send completed", extra=summary)
    return {"sent": sent, "failed": failed, "requested": len(items)}
