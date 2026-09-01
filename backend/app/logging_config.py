"""Centralized logging for the whole backend — web app and Celery workers alike.

Every module gets its logger from `get_logger(__name__)`; nothing calls
`logging.basicConfig` on its own. `setup_logging()` is the single place that
decides level, format and handler, and it is invoked from exactly two entry
points: `app.main` (uvicorn) and the Celery `setup_logging` signal in
`app.tasks.celery_app` (workers and beat). Before this module existed the
worker tier silently ran on Celery's own default format, because `basicConfig`
only ran when `app.main` was imported.

Two conventions the rest of the codebase relies on:

**Correlation.** `bind_request_context()` stores a request ID in a ContextVar.
ContextVars are isolated per asyncio task and survive `await`, so one `set()` in
the HTTP middleware makes that ID visible to every log line the request emits —
including ones several service layers deep — with no parameter threading. The
same slot carries a Celery task ID inside workers, so a submission can be traced
from the student's HTTP POST through to the AI evaluation that ran minutes later.

**Redaction.** This app handles passwords, JWTs, magic-link tokens, exam PINs and
the SEB Config Key. None of them may ever reach a log sink. `redact()` scrubs
mappings by key name and `fingerprint()` turns a secret into a short, stable,
non-reversible tag that is still comparable across log lines — which is all an
operator actually needs when diagnosing "why doesn't this key match".
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import uuid
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

# --------------------------------------------------------------- correlation

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_actor: ContextVar[str | None] = ContextVar("actor", default=None)


def new_request_id() -> str:
    """A fresh correlation ID. Short — these are read off a terminal, not parsed."""
    return uuid.uuid4().hex[:16]


def bind_request_context(request_id: str | None = None, actor: str | None = None) -> None:
    """Attach a correlation ID (and optionally an actor) to the current context."""
    if request_id is not None:
        _request_id.set(request_id)
    if actor is not None:
        _actor.set(actor)


def clear_request_context() -> None:
    _request_id.set(None)
    _actor.set(None)


def get_request_id() -> str | None:
    return _request_id.get()


# ----------------------------------------------------------------- redaction

# Substring match, lowercased, against both dict keys and log-extra field names.
SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "session",
    "pin",
    "config_key",
    "credential",
    "private",
    "hash",
)

REDACTED = "***redacted***"


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively replace sensitive-looking values in a mapping or sequence.

    Depth-bounded: log formatting must never become the slow or recursive part
    of a request, and nothing this app logs is legitimately nested that deep.
    """
    if _depth > 4:
        return "..."
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if is_sensitive_key(str(k)) else redact(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(v, _depth + 1) for v in value]
    return value


def fingerprint(secret: str | None) -> str:
    """A short, stable, non-reversible tag for a secret.

    Lets an operator confirm "the key the exam holds is not the key SEB sent"
    without either value ever appearing in a log.
    """
    if not secret:
        return "none"
    return "sha256:" + hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]


def mask_email(email: str | None) -> str:
    """`alice@example.com` -> `a***e@example.com`.

    Enough to correlate repeated failed logins from one address across lines,
    without writing the full address — which is PII — into the log.
    """
    if not email or "@" not in email:
        return "unknown"
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        return f"{local[:1]}***@{domain}"
    return f"{local[0]}***{local[-1]}@{domain}"


# ----------------------------------------------------------------- formatting

# Attributes present on every LogRecord. Anything outside this set arrived via
# `extra=` and is structured context worth emitting as its own JSON field.
_RESERVED = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "taskName", "thread", "threadName",
    }
)


class ContextFilter(logging.Filter):
    """Stamps the current correlation ID and actor onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.actor = _actor.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line.

    Built with `json.dumps`, not string interpolation — the previous format
    string embedded `%(message)s` raw inside a JSON literal, so any message
    containing a quote or backslash emitted a broken line that no log shipper
    could parse.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        actor = getattr(record, "actor", None)
        if actor:
            payload["actor"] = actor

        for key, value in record.__dict__.items():
            if key in _RESERVED or key in ("request_id", "actor") or key.startswith("_"):
                continue
            payload[key] = REDACTED if is_sensitive_key(key) else redact(value)

        if record.exc_info:
            payload["exc_type"] = getattr(record.exc_info[0], "__name__", "Exception")
            payload["traceback"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # default=str so a UUID or datetime in an `extra` can never take down
        # the logging call that was meant to help debug something else.
        return json.dumps(payload, default=str, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single line for local development."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        request_id = getattr(record, "request_id", None)
        extras = {
            k: (REDACTED if is_sensitive_key(k) else v)
            for k, v in record.__dict__.items()
            if k not in _RESERVED and k not in ("request_id", "actor") and not k.startswith("_")
        }
        parts = [base]
        if request_id:
            parts.append(f"[rid={request_id}]")
        if extras:
            parts.append(" ".join(f"{k}={v}" for k, v in extras.items()))
        return " ".join(parts)


# --------------------------------------------------------------------- setup

_configured = False

# Libraries that are chatty at INFO and say nothing an operator of *this* app
# needs. uvicorn.access is muted because app.main emits a richer access line
# that carries the correlation ID; leaving both on double-logs every request.
_NOISY = {
    "uvicorn.access": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "sqlalchemy.engine": logging.WARNING,
    "aiosqlite": logging.WARNING,
    "multipart": logging.WARNING,
    "asyncio": logging.WARNING,
}


def setup_logging(force: bool = False) -> None:
    """Install the root handler. Idempotent — safe to call from several entry points."""
    global _configured
    if _configured and not force:
        return

    # Imported here rather than at module scope: app.config runs validation at
    # import time and can raise, and a logging module that cannot be imported
    # would leave the failure itself unloggable.
    from app.config import settings

    level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
    use_json = str(settings.log_format).lower() != "console"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if use_json
        else ConsoleFormatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s", "%H:%M:%S")
    )
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    for name, noisy_level in _NOISY.items():
        logging.getLogger(name).setLevel(max(noisy_level, level))

    if settings.log_sql:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """The only logger factory the app should use."""
    return logging.getLogger(name)
