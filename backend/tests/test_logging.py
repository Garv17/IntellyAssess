"""Tests for the centralized logger.

These exist because the two things this module must never do — emit a line a log
shipper cannot parse, and write a secret to disk — both fail silently. Nothing
downstream would notice either one until an incident, which is exactly when the
logs need to work.
"""

from __future__ import annotations

import json
import logging
import sys

import pytest

from app.logging_config import (
    REDACTED,
    ContextFilter,
    JsonFormatter,
    bind_request_context,
    clear_request_context,
    fingerprint,
    is_sensitive_key,
    mask_email,
    redact,
)


def _record(msg: str, level: int = logging.INFO, **extra) -> logging.LogRecord:
    record = logging.LogRecord("test.logger", level, __file__, 1, msg, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    ContextFilter().filter(record)
    return record


def _emit(msg: str, **extra) -> dict:
    """Format a record the way the real handler would and parse it back."""
    return json.loads(JsonFormatter().format(_record(msg, **extra)))


@pytest.fixture(autouse=True)
def _clean_context():
    clear_request_context()
    yield
    clear_request_context()


# ------------------------------------------------------------- JSON validity


def test_message_containing_quotes_still_parses():
    """The previous formatter interpolated the message into a JSON string
    literal, so a quote in a message produced a line no shipper could read.
    seb.py logged %r reprs through it, which meant this happened in practice."""
    payload = _emit('hash mismatch received="abc" expected=\'def\'')
    assert payload["msg"] == 'hash mismatch received="abc" expected=\'def\''


def test_message_containing_backslashes_and_newlines_still_parses():
    payload = _emit("windows path C:\\Users\\x\nsecond line\ttabbed")
    assert payload["msg"] == "windows path C:\\Users\\x\nsecond line\ttabbed"


def test_non_serializable_extra_does_not_break_the_line():
    """A UUID or datetime in an `extra` must never take down the logging call
    that was meant to help debug something else."""
    class Opaque:
        def __str__(self) -> str:
            return "opaque-value"

    payload = _emit("with object", thing=Opaque())
    assert payload["thing"] == "opaque-value"


def test_exception_is_recorded_with_type_and_traceback():
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord(
            "test.logger", logging.ERROR, __file__, 1, "failed", (), sys.exc_info()
        )
        ContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))

    assert payload["exc_type"] == "ValueError"
    assert "ValueError: boom" in payload["traceback"]


# ------------------------------------------------------------------ redaction


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "password_hash",
        "refresh_token",
        "access_token",
        "jwt_secret",
        "openai_api_key",
        "authorization",
        "pin_hash",
        "seb_config_key",
        "Cookie",
    ],
)
def test_sensitive_keys_are_recognised(key):
    assert is_sensitive_key(key)


@pytest.mark.parametrize("key", ["exam_id", "student_id", "duration_ms", "status_code", "reason"])
def test_ordinary_keys_are_not_redacted(key):
    assert not is_sensitive_key(key)


def test_sensitive_extra_field_never_reaches_the_output():
    payload = _emit("login", password="hunter2", student_id="abc")
    assert payload["password"] == REDACTED
    assert "hunter2" not in json.dumps(payload)
    assert payload["student_id"] == "abc"


def test_nested_sensitive_values_are_scrubbed():
    payload = _emit("request", body={"email": "a@b.com", "password": "hunter2"})
    assert payload["body"]["password"] == REDACTED
    assert payload["body"]["email"] == "a@b.com"


def test_redact_is_depth_bounded():
    """Log formatting must never become the recursive part of a request."""
    deep: dict = {}
    cursor = deep
    for _ in range(12):
        cursor["next"] = {}
        cursor = cursor["next"]
    cursor["password"] = "hunter2"

    assert "hunter2" not in json.dumps(redact(deep))


def test_redact_handles_sequences():
    assert redact([{"token": "t"}, {"exam_id": "e"}]) == [{"token": REDACTED}, {"exam_id": "e"}]


# --------------------------------------------------------------- fingerprints


def test_fingerprint_is_stable_and_not_reversible():
    secret = "the-seb-config-key"
    assert fingerprint(secret) == fingerprint(secret)
    assert secret not in fingerprint(secret)
    assert fingerprint(secret) != fingerprint(secret + "x")


def test_fingerprint_of_nothing_is_a_marker_not_a_hash():
    assert fingerprint(None) == "none"
    assert fingerprint("") == "none"


def test_mask_email_hides_the_local_part():
    assert mask_email("alice@example.com") == "a***e@example.com"
    # Short local parts must not round-trip to the original.
    assert mask_email("ab@example.com") == "a***@example.com"
    assert mask_email(None) == "unknown"
    assert mask_email("not-an-email") == "unknown"


# --------------------------------------------------------------- correlation


def test_request_id_is_attached_to_records_once_bound():
    bind_request_context(request_id="abc123", actor="admin:42")
    payload = _emit("something happened")
    assert payload["request_id"] == "abc123"
    assert payload["actor"] == "admin:42"


def test_records_carry_no_correlation_fields_when_unbound():
    payload = _emit("something happened")
    assert "request_id" not in payload
    assert "actor" not in payload
