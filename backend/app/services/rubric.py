"""Loads the marking rubric that the AI evaluator scores against.

The rubric lives in a YAML file, not in the prompt, so the marking scheme can be
changed (or swapped per question, later) without editing any code or prompt text.
Everything the model returns is validated against the caps defined here — the
prompt is a request, this file is the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

# backend/ — the Docker image's WORKDIR, and the repo dir when run locally.
BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Criterion:
    key: str
    marks: float
    description: str


@dataclass(frozen=True)
class Rubric:
    version: str
    max_marks: float
    criteria: tuple[Criterion, ...]

    def cap(self, key: str) -> float | None:
        for criterion in self.criteria:
            if criterion.key == key:
                return criterion.marks
        return None

    def as_prompt_block(self) -> str:
        """The rubric as the evaluator sees it — mirrors the validation below, so
        the model is asked for exactly the shape that will be accepted."""
        lines = [f"Total marks available: {self.max_marks:g}", ""]
        for criterion in self.criteria:
            lines.append(f"- {criterion.key} (max {criterion.marks:g} marks): {criterion.description}")
        return "\n".join(lines)


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else BACKEND_ROOT / path


def load_rubric(path_str: str | None = None) -> Rubric:
    path = _resolve(path_str or settings.coding_rubric_path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        # A deploy/config fault, not a data fault: get_rubric() is lru_cached and
        # called per submission, so every coding evaluation on this worker fails
        # until the file is in place. The resolved absolute path is the whole
        # diagnosis — it is almost always a bind-mount or WORKDIR mismatch.
        log.error(
            "coding rubric file missing",
            extra={"rubric_path": str(path), "configured": settings.coding_rubric_path},
        )
        raise RuntimeError(f"Coding rubric not found at {path}") from exc

    criteria_raw = raw.get("criteria") or {}
    if not criteria_raw:
        log.error("coding rubric defines no criteria", extra={"rubric_path": str(path)})
        raise RuntimeError(f"Rubric {path} defines no criteria")

    criteria = tuple(
        Criterion(
            key=key,
            marks=float(body["marks"]),
            description=str(body.get("description", "")).strip(),
        )
        for key, body in criteria_raw.items()
    )

    max_marks = float(raw.get("max_marks", 0))
    total = sum(c.marks for c in criteria)
    if abs(total - max_marks) > 1e-6:
        # A rubric whose parts don't add up to its whole would silently cap every
        # submission below (or let it exceed) the advertised maximum.
        log.error(
            "coding rubric marks do not add up",
            extra={"rubric_path": str(path), "criteria_total": total, "max_marks": max_marks},
        )
        raise RuntimeError(
            f"Rubric {path}: criteria marks sum to {total:g} but max_marks is {max_marks:g}"
        )

    # Once per worker process (get_rubric is lru_cached): pins down which marking
    # scheme a batch of evaluations was actually graded against.
    log.info(
        "coding rubric loaded",
        extra={
            "rubric_path": str(path),
            "rubric_version": str(raw.get("version", "1")),
            "max_marks": max_marks,
            "criteria": [c.key for c in criteria],
        },
    )
    return Rubric(version=str(raw.get("version", "1")), max_marks=max_marks, criteria=criteria)


@lru_cache
def get_rubric() -> Rubric:
    """Process-cached. Editing the YAML needs a worker restart to take effect,
    which is the right trade for a file read on every submission."""
    return load_rubric()
