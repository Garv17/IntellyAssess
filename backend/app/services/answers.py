"""Answer persistence — the hot path.

Normal flow: PATCH -> Redis hash + dirty set -> 200. A Celery beat task flushes
to Postgres every few seconds. If Redis is unavailable the same payload is
upserted directly, so correctness never depends on the cache being up.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models import Answer

log = get_logger(__name__)

SAVEABLE_FIELDS = ("selected_option_id", "code_text", "language", "is_marked_for_review")


def payload_from_schema(item: Any) -> dict[str, Any]:
    return {
        "selected_option_id": str(item.selected_option_id) if item.selected_option_id else None,
        "code_text": item.code_text,
        "language": item.language,
        "is_marked_for_review": item.is_marked_for_review,
    }


async def upsert_answers(
    db: AsyncSession, attempt_id: uuid.UUID, answers: dict[str, dict[str, Any]]
) -> int:
    """Bulk idempotent upsert keyed on (attempt_id, question_id)."""
    if not answers:
        return 0

    rows = []
    for question_id, payload in answers.items():
        option_id = payload.get("selected_option_id")
        rows.append(
            {
                "id": uuid.uuid4(),
                "attempt_id": attempt_id,
                "question_id": uuid.UUID(str(question_id)),
                "selected_option_id": uuid.UUID(option_id) if option_id else None,
                "code_text": payload.get("code_text"),
                "language": payload.get("language"),
                "is_marked_for_review": bool(payload.get("is_marked_for_review", False)),
                "score": 0.0,
            }
        )

    stmt = insert(Answer).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_answer_attempt_question",
        set_={
            "selected_option_id": stmt.excluded.selected_option_id,
            "code_text": stmt.excluded.code_text,
            "language": stmt.excluded.language,
            "is_marked_for_review": stmt.excluded.is_marked_for_review,
        },
    )
    await db.execute(stmt)
    # One line per upsert call, never per row. This is the direct-to-Postgres
    # path, so a steady stream of these means the Redis buffer is not carrying
    # autosave any more — worth being able to see.
    log.info(
        "answers persisted",
        extra={"attempt_id": str(attempt_id), "answers_written": len(rows)},
    )
    return len(rows)


async def load_answers(db: AsyncSession, attempt_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    result = await db.execute(select(Answer).where(Answer.attempt_id == attempt_id))
    return {
        str(row.question_id): {
            "selected_option_id": str(row.selected_option_id) if row.selected_option_id else None,
            "code_text": row.code_text,
            "language": row.language,
            "is_marked_for_review": row.is_marked_for_review,
        }
        for row in result.scalars()
    }


def merge(
    persisted: dict[str, dict[str, Any]], buffered: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Buffer wins — it is by definition newer than what has been flushed."""
    merged = dict(persisted)
    merged.update(buffered)
    return merged
