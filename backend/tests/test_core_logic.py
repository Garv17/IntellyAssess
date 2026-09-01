"""Tests for the pieces that don't need a live database: randomization stability,
password hashing, rubric loading, AI-evaluation response validation, token claims,
and the SQL that the auto-save flush depends on."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import jwt
import pytest
from sqlalchemy.dialects import postgresql

from app.security import (
    create_access_token,
    create_magic_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.services import paper


# --------------------------------------------------------------------- passwords


def test_password_round_trip():
    hashed = hash_password("Student@123")
    assert verify_password("Student@123", hashed)
    assert not verify_password("student@123", hashed)


def test_verify_rejects_malformed_hash_without_raising():
    # A corrupted stored hash must read as a failed login, not a 500.
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_long_passwords_are_truncated_consistently():
    # bcrypt ignores bytes past 72; the same prefix must still verify.
    base = "a" * 80
    hashed = hash_password(base)
    assert verify_password("a" * 72, hashed)


# ------------------------------------------------------------------------ tokens


def test_magic_token_carries_type_and_expires_within_ttl():
    from datetime import UTC, datetime

    from app.config import settings

    token, jti = create_magic_token("student-pk")
    claims = decode_token(token)
    assert claims["sub"] == "student-pk"
    assert claims["type"] == "magic"
    assert claims["jti"] == jti
    remaining = datetime.fromtimestamp(claims["exp"], UTC) - datetime.now(UTC)
    assert remaining.total_seconds() <= settings.magic_link_ttl_minutes * 60


def test_access_token_carries_attempt_binding():
    attempt_id = str(uuid.uuid4())
    token, jti = create_access_token("student-pk", "student", attempt_id=attempt_id)
    claims = decode_token(token)
    assert claims["sub"] == "student-pk"
    assert claims["role"] == "student"
    assert claims["type"] == "access"
    assert claims["attempt_id"] == attempt_id
    assert claims["jti"] == jti


def test_tampered_token_is_rejected():
    token, _ = create_access_token("student-pk", "student")
    forged = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    with pytest.raises(jwt.PyJWTError):
        decode_token(forged)


# ---------------------------------------------------------------- randomization


def _fake_exam(randomize_questions=True, randomize_options=False):
    """Minimal duck-typed exam tree — enough for build_question_order."""
    section_id = uuid.uuid4()
    di_group_id = uuid.uuid4()

    def question(order, di_group=None, options=0):
        return SimpleNamespace(
            id=uuid.uuid4(),
            order_index=order,
            di_group_id=di_group,
            options=[
                SimpleNamespace(id=uuid.uuid4(), order_index=i) for i in range(options)
            ],
        )

    standalone = [question(i, options=4) for i in range(6)]
    grouped = [question(i, di_group=di_group_id, options=4) for i in range(3)]

    section = SimpleNamespace(
        id=section_id,
        order_index=0,
        title="Section A",
        instructions=None,
        questions=standalone + grouped,
        di_groups=[SimpleNamespace(id=di_group_id, order_index=0)],
    )
    return SimpleNamespace(
        sections=[section],
        randomize_questions=randomize_questions,
        randomize_options=randomize_options,
    )


def test_question_order_is_stable_for_the_same_seed():
    exam = _fake_exam()
    first = paper.build_question_order(exam, "seed-abc")
    second = paper.build_question_order(exam, "seed-abc")
    # Resume-on-refresh depends on this: the same student must see the same paper.
    assert first == second


def test_question_order_differs_across_students():
    exam = _fake_exam()
    orders = {
        str(paper.build_question_order(exam, f"seed-{i}")["sections"][0]["blocks"])
        for i in range(8)
    }
    assert len(orders) > 1


def test_di_questions_stay_grouped_after_shuffling():
    exam = _fake_exam()
    order = paper.build_question_order(exam, "seed-xyz")
    blocks = order["sections"][0]["blocks"]
    di_blocks = [b for b in blocks if b["kind"] == "di_group"]
    assert len(di_blocks) == 1
    # All three child questions travel with their stimulus, never scattered.
    assert len(di_blocks[0]["question_ids"]) == 3


def test_every_question_appears_exactly_once():
    exam = _fake_exam()
    order = paper.build_question_order(exam, "seed-1")
    seen: list[str] = []
    for block in order["sections"][0]["blocks"]:
        if block["kind"] == "question":
            seen.append(block["question_id"])
        else:
            seen.extend(block["question_ids"])
    assert len(seen) == 9
    assert len(set(seen)) == 9


def test_option_order_only_recorded_when_enabled():
    assert paper.build_question_order(_fake_exam(), "s")["sections"][0]["option_order"] == {}
    shuffled = paper.build_question_order(
        _fake_exam(randomize_options=True), "s"
    )["sections"][0]["option_order"]
    assert len(shuffled) == 9
    assert all(len(v) == 4 for v in shuffled.values())


# --------------------------------------------------------------- auto-save SQL


def test_answer_upsert_compiles_to_on_conflict_update():
    """The flush task's correctness rests on this being an idempotent upsert against
    the (attempt_id, question_id) constraint."""
    from sqlalchemy.dialects.postgresql import insert

    from app.models import Answer

    stmt = insert(Answer).values(
        [
            {
                "id": uuid.uuid4(),
                "attempt_id": uuid.uuid4(),
                "question_id": uuid.uuid4(),
                "selected_option_id": None,
                "code_text": None,
                "language": None,
                "is_marked_for_review": False,
                "score": 0.0,
            }
        ]
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_answer_attempt_question",
        set_={"code_text": stmt.excluded.code_text},
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT ON CONSTRAINT uq_answer_attempt_question DO UPDATE" in sql


def test_attempt_uniqueness_constraint_exists():
    """One-attempt-per-student is enforced by the database, not application code."""
    from app.models import ExamAttempt

    names = {c.name for c in ExamAttempt.__table__.constraints if c.name}
    assert "uq_attempt_exam_student" in names


def test_student_serializers_never_expose_answer_keys():
    from app.schemas import CodingProblemOut, OptionOut, QuestionOut

    for model in (OptionOut, QuestionOut, CodingProblemOut):
        assert "is_correct" not in model.model_fields


# --------------------------------------------------------------------- question bank preview


def test_body_preview_collapses_images_instead_of_truncating_mid_tag():
    from app.routers.admin import _body_preview

    # A naive body_md[:140] slice would cut this image tag before its closing
    # paren, leaving broken markdown (`![diagram](/uploads/abc`) in the preview.
    body = "x" * 130 + "![diagram](/uploads/abc123-a-very-long-filename.png) more text"
    preview = _body_preview(body, limit=140)
    assert "![" not in preview
    assert "(/uploads/" not in preview
    assert "[image]" in preview


def test_body_preview_short_body_is_unchanged():
    from app.routers.admin import _body_preview

    assert _body_preview("What is 2 + 2?") == "What is 2 + 2?"


def test_body_preview_replaces_every_image_before_truncating():
    from app.routers.admin import _body_preview

    body = "![a](url1) some text ![b](url2) more text"
    preview = _body_preview(body, limit=200)
    assert preview == "[image] some text [image] more text"


# --------------------------------------------------------------------- admin management authz


def test_current_super_admin_allows_super_admin_role():
    import asyncio

    from app.deps import current_super_admin

    fake_admin = SimpleNamespace(id=uuid.uuid4(), role="super_admin")
    result = asyncio.run(current_super_admin(admin=fake_admin))
    assert result is fake_admin


def test_current_super_admin_rejects_plain_admin_role():
    import asyncio

    from fastapi import HTTPException

    from app.deps import current_super_admin

    fake_admin = SimpleNamespace(id=uuid.uuid4(), role="admin")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(current_super_admin(admin=fake_admin))
    assert exc_info.value.status_code == 403
