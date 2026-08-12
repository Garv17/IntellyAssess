"""Transactional email via Brevo's HTTP API — no SMTP client needed, httpx already
covers it.

Two send paths: async (the web app, one email per request) and sync (Celery workers,
which run no event loop — see app/sync_db.py for why that's a separate world)."""

from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


def _magic_link_payload(to_email: str, to_name: str, link: str) -> dict:
    return {
        "sender": {"name": settings.brevo_sender_name, "email": settings.brevo_sender_email},
        "to": [{"email": to_email, "name": to_name}],
        "subject": f"Your {settings.app_name} sign-in link",
        "htmlContent": (
            f"<p>Hi {to_name},</p>"
            f'<p><a href="{link}">Click here to sign in to {settings.app_name}</a>.</p>'
            f"<p>This link expires in {settings.magic_link_ttl_minutes} minutes and can only "
            "be used once.</p>"
            "<p>If you didn't request this, you can safely ignore this email.</p>"
        ),
    }


def _headers() -> dict:
    return {"api-key": settings.brevo_api_key, "content-type": "application/json"}


async def send_magic_link_email(to_email: str, to_name: str, link: str) -> None:
    payload = _magic_link_payload(to_email, to_name, link)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(BREVO_ENDPOINT, json=payload, headers=_headers())
    if response.status_code >= 300:
        log.error("brevo send failed: %s %s", response.status_code, response.text)
        response.raise_for_status()


def send_magic_link_email_sync(to_email: str, to_name: str, link: str) -> None:
    """Same call, blocking — for Celery tasks (app/tasks/mailer_tasks.py)."""
    payload = _magic_link_payload(to_email, to_name, link)
    with httpx.Client(timeout=10) as client:
        response = client.post(BREVO_ENDPOINT, json=payload, headers=_headers())
    if response.status_code >= 300:
        log.error("brevo send failed: %s %s", response.status_code, response.text)
        response.raise_for_status()


def _invite_payload(to_email: str, to_name: str, exam_title: str, link: str) -> dict:
    return {
        "sender": {"name": settings.brevo_sender_name, "email": settings.brevo_sender_email},
        "to": [{"email": to_email, "name": to_name}],
        "subject": f"Your Safe Exam Browser PIN for {exam_title}",
        "htmlContent": (
            f"<p>Hi {to_name},</p>"
            f'<p><a href="{link}">Open this link</a> to get your sign-in PIN for '
            f"{exam_title}.</p>"
            "<p>This link is safe to open in your normal browser or email app — it never "
            "signs you in by itself. You'll type the PIN it shows you once Safe Exam "
            "Browser has launched.</p>"
        ),
    }


def send_invite_email_sync(to_email: str, to_name: str, exam_title: str, link: str) -> None:
    """Blocking — used by the Celery bulk-invite task, same reasoning as the magic-link
    sync variant above."""
    payload = _invite_payload(to_email, to_name, exam_title, link)
    with httpx.Client(timeout=10) as client:
        response = client.post(BREVO_ENDPOINT, json=payload, headers=_headers())
    if response.status_code >= 300:
        log.error("brevo send failed: %s %s", response.status_code, response.text)
        response.raise_for_status()
