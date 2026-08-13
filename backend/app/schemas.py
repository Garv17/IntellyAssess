from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import AttemptStatus, EvaluationStatus, ExamStatus, QuestionType


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------- auth


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkVerify(BaseModel):
    token: str = Field(min_length=1)


class MagicLinkSent(BaseModel):
    message: str = "If that email is registered, a sign-in link is on its way."
    # Only ever populated when ENVIRONMENT=development, so a local sign-in can
    # redeem a link without a real mailbox. Never set outside dev — see
    # request_magic_link.
    dev_token: str | None = None


class AdminLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class MeOut(ORMModel):
    id: uuid.UUID
    name: str
    role: str
    identifier: str


# ------------------------------------------------------- student exam views
# Note: no serializer below ever exposes is_correct or hidden test cases.


class OptionOut(ORMModel):
    id: uuid.UUID
    body: str


class TestCaseOut(ORMModel):
    id: uuid.UUID
    stdin: str
    expected_stdout: str
    param_values: list[Any] | None = None
    expected_value: Any | None = None
    explanation: str | None = None


class CodingProblemOut(ORMModel):
    id: uuid.UUID
    statement_md: str
    constraints_md: str | None = None
    allowed_languages: list[str]
    time_limit_ms: int
    memory_limit_mb: int
    starter_code: dict[str, str]
    problem_type: Literal["stdio", "function"] = "stdio"
    function_name: str | None = None
    return_type: str | None = None
    parameters: list[dict[str, str]] | None = None
    sql_dialect: str | None = None
    sql_schema_sql: str | None = None
    sql_result_columns: list[str] | None = None
    sample_test_cases: list[TestCaseOut] = []


class DIGroupOut(ORMModel):
    id: uuid.UUID
    title: str
    passage_md: str | None
    image_url: str | None


class QuestionOut(ORMModel):
    id: uuid.UUID
    type: QuestionType
    body_md: str
    marks: float
    negative_marks: float
    di_group_id: uuid.UUID | None = None
    options: list[OptionOut] = []
    coding_problem: CodingProblemOut | None = None


class SectionOut(ORMModel):
    id: uuid.UUID
    title: str
    instructions: str | None
    order_index: int
    questions: list[QuestionOut] = []
    di_groups: list[DIGroupOut] = []


class ExamPaperOut(BaseModel):
    exam_id: uuid.UUID
    title: str
    instructions_md: str | None
    duration_minutes: int
    sections: list[SectionOut]


class ExamSummaryOut(ORMModel):
    id: uuid.UUID
    title: str
    description: str | None
    duration_minutes: int
    starts_at: datetime | None
    ends_at: datetime | None
    status: ExamStatus
    attempt_status: AttemptStatus | None = None
    requires_seb: bool = False


# --------------------------------------------------------------- attempts


class AnswerState(BaseModel):
    question_id: uuid.UUID
    selected_option_id: uuid.UUID | None = None
    code_text: str | None = None
    language: str | None = None
    is_marked_for_review: bool = False


class ExamStateOut(BaseModel):
    attempt_id: uuid.UUID
    exam_id: uuid.UUID
    status: AttemptStatus
    started_at: datetime
    deadline_at: datetime
    seconds_remaining: int
    question_order: dict[str, Any]
    answers: list[AnswerState]


class AnswerSave(BaseModel):
    question_id: uuid.UUID
    selected_option_id: uuid.UUID | None = None
    code_text: str | None = Field(default=None, max_length=65536)
    language: str | None = Field(default=None, max_length=32)
    is_marked_for_review: bool = False


class AnswerBatchSave(BaseModel):
    answers: list[AnswerSave] = Field(min_length=1, max_length=200)


class SaveAck(BaseModel):
    saved: int
    seconds_remaining: int
    server_time: datetime


class HeartbeatOut(BaseModel):
    seconds_remaining: int
    status: AttemptStatus
    server_time: datetime


class SubmitReceipt(BaseModel):
    attempt_id: uuid.UUID
    status: AttemptStatus
    submitted_at: datetime
    answered_count: int
    total_questions: int
    receipt_code: str
    grading_pending: bool


# ------------------------------------------------------------------- admin


class ExamCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    instructions_md: str | None = None
    duration_minutes: int = Field(default=60, ge=1, le=600)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    randomize_questions: bool = True
    randomize_options: bool = True
    cohort: str | None = None
    pass_percentage: float | None = Field(default=None, ge=0, le=100)
    requires_seb: bool = False
    seb_config_key: str | None = Field(default=None, max_length=64)


class ExamOut(ORMModel):
    id: uuid.UUID
    title: str
    description: str | None
    instructions_md: str | None
    duration_minutes: int
    status: ExamStatus
    starts_at: datetime | None
    ends_at: datetime | None
    randomize_questions: bool
    randomize_options: bool
    cohort: str | None
    pass_percentage: float | None
    created_at: datetime
    requires_seb: bool
    seb_config_key: str | None


class SectionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    instructions: str | None = None
    order_index: int = 0
    marks_per_question: float = 1.0
    negative_marks: float = 0.0


class SectionAdminOut(ORMModel):
    id: uuid.UUID
    exam_id: uuid.UUID
    title: str
    instructions: str | None
    order_index: int
    marks_per_question: float
    negative_marks: float
    question_count: int = 0


class OptionCreate(BaseModel):
    body: str = Field(min_length=1)
    is_correct: bool = False
    order_index: int = 0


class MCQCreate(BaseModel):
    body_md: str = Field(min_length=1)
    explanation_md: str | None = None
    marks: float | None = None
    negative_marks: float | None = None
    order_index: int = 0
    options: list[OptionCreate] = Field(min_length=2, max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=10)
    difficulty: Literal["easy", "medium", "hard"] | None = None


class DIQuestionCreate(BaseModel):
    body_md: str = Field(min_length=1)
    explanation_md: str | None = None
    marks: float | None = None
    order_index: int = 0
    options: list[OptionCreate] = Field(min_length=2, max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=10)
    difficulty: Literal["easy", "medium", "hard"] | None = None


class DIGroupCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    passage_md: str | None = None
    image_url: str | None = None
    order_index: int = 0
    questions: list[DIQuestionCreate] = Field(min_length=1)


# The languages a student may pick in the editor. Nothing compiles or runs them
# on this branch — the choice is stored with the submission and shown to the AI
# evaluator and the reviewing admin.
CodingLanguage = Literal["python", "javascript", "c", "cpp", "java", "sql"]

# Display metadata: labels the editor and tells the evaluator which dialect the
# answer is expected to be written in.
SqlDialect = Literal["sqlite", "mysql", "postgresql", "mssql"]

# Must match app.services.harness.PARAM_TYPES — that's what renders each one into
# the per-language starter code.
ParamType = Literal[
    "int", "float", "bool", "string", "char",
    "int[]", "float[]", "bool[]", "string[]", "int[][]",
]


class ParamDef(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: ParamType


class BoilerplatePreviewRequest(BaseModel):
    language: str
    function_name: str = Field(min_length=1, max_length=128)
    return_type: ParamType
    parameters: list[ParamDef] = Field(min_length=1)


class BoilerplatePreviewOut(BaseModel):
    code: str


class TestCaseCreate(BaseModel):
    stdin: str = ""
    expected_stdout: str = ""
    # Function-mode equivalents of stdin/expected_stdout above.
    param_values: list[Any] | None = None
    expected_value: Any | None = None
    explanation: str | None = None
    is_sample: bool = False
    weight: int = Field(default=1, ge=1)
    order_index: int = 0


class CodingCreate(BaseModel):
    body_md: str = Field(min_length=1)
    statement_md: str = Field(min_length=1)
    constraints_md: str | None = None
    marks: float | None = None
    order_index: int = 0
    allowed_languages: list[CodingLanguage] = ["python"]
    time_limit_ms: int = Field(default=2000, ge=100, le=15000)
    memory_limit_mb: int = Field(default=256, ge=16, le=512)
    # Ignored server-side for problem_type="function" — starter_code is always
    # derived from function_name/return_type/parameters in that case.
    starter_code: dict[str, str] = {}
    problem_type: Literal["stdio", "function"] = "stdio"
    function_name: str | None = Field(default=None, max_length=128)
    return_type: ParamType | None = None
    parameters: list[ParamDef] | None = None
    test_cases: list[TestCaseCreate] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list, max_length=10)
    difficulty: Literal["easy", "medium", "hard"] | None = None
    # SQL-only — see CodingProblem.sql_dialect/sql_schema_sql/sql_result_columns.
    sql_dialect: SqlDialect | None = None
    sql_schema_sql: str | None = None
    sql_result_columns: list[str] | None = None


class QuestionAdminOut(ORMModel):
    id: uuid.UUID
    section_id: uuid.UUID
    type: QuestionType
    body_md: str
    explanation_md: str | None
    marks: float | None
    order_index: int
    di_group_id: uuid.UUID | None
    tags: list[str] = []
    difficulty: str | None = None


# --------------------------------------------------------- admin edit views
# Unlike the student-facing serializers above, these expose is_correct and
# hidden test cases — the whole point is letting an admin see and change them.


class OptionAdminOut(ORMModel):
    id: uuid.UUID
    body: str
    is_correct: bool
    order_index: int


class TestCaseAdminOut(ORMModel):
    id: uuid.UUID
    stdin: str
    expected_stdout: str
    param_values: list[Any] | None = None
    expected_value: Any | None = None
    explanation: str | None = None
    is_sample: bool
    weight: int
    order_index: int


class CodingProblemAdminOut(ORMModel):
    id: uuid.UUID
    statement_md: str
    constraints_md: str | None = None
    allowed_languages: list[str]
    time_limit_ms: int
    memory_limit_mb: int
    starter_code: dict[str, str]
    problem_type: Literal["stdio", "function"] = "stdio"
    function_name: str | None = None
    return_type: str | None = None
    parameters: list[dict[str, str]] | None = None
    sql_dialect: str | None = None
    sql_schema_sql: str | None = None
    sql_result_columns: list[str] | None = None
    test_cases: list[TestCaseAdminOut] = []


class QuestionDetailOut(ORMModel):
    id: uuid.UUID
    section_id: uuid.UUID
    type: QuestionType
    body_md: str
    explanation_md: str | None
    marks: float | None
    negative_marks: float | None
    order_index: int
    di_group_id: uuid.UUID | None
    options: list[OptionAdminOut] = []
    coding_problem: CodingProblemAdminOut | None = None
    tags: list[str] = []
    difficulty: str | None = None


class DIGroupAdminOut(ORMModel):
    id: uuid.UUID
    section_id: uuid.UUID
    title: str
    passage_md: str | None
    image_url: str | None
    order_index: int
    questions: list[QuestionDetailOut] = []


class MCQUpdate(BaseModel):
    body_md: str = Field(min_length=1)
    explanation_md: str | None = None
    marks: float | None = None
    negative_marks: float | None = None
    options: list[OptionCreate] = Field(min_length=2, max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=10)
    difficulty: Literal["easy", "medium", "hard"] | None = None


class DIGroupUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    passage_md: str | None = None
    image_url: str | None = None


class CodingUpdate(BaseModel):
    body_md: str = Field(min_length=1)
    statement_md: str = Field(min_length=1)
    constraints_md: str | None = None
    marks: float | None = None
    allowed_languages: list[CodingLanguage] = ["python"]
    time_limit_ms: int = Field(default=2000, ge=100, le=15000)
    memory_limit_mb: int = Field(default=256, ge=16, le=512)
    starter_code: dict[str, str] = {}
    problem_type: Literal["stdio", "function"] = "stdio"
    function_name: str | None = Field(default=None, max_length=128)
    return_type: ParamType | None = None
    parameters: list[ParamDef] | None = None
    test_cases: list[TestCaseCreate] = Field(min_length=1)
    tags: list[str] = Field(default_factory=list, max_length=10)
    difficulty: Literal["easy", "medium", "hard"] | None = None
    sql_dialect: SqlDialect | None = None
    sql_schema_sql: str | None = None
    sql_result_columns: list[str] | None = None


class StudentCreate(BaseModel):
    student_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    cohort: str | None = None


class StudentOut(ORMModel):
    id: uuid.UUID
    student_id: str
    name: str
    email: str
    cohort: str | None
    is_active: bool


class StudentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    email: EmailStr | None = None
    cohort: str | None = None
    is_active: bool | None = None


class BulkResult(BaseModel):
    created: int
    skipped: int
    errors: list[str] = []


class BulkMagicLinkRequest(BaseModel):
    student_ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)


class BulkMagicLinkQueued(BaseModel):
    queued: int


class InviteCreateRequest(BaseModel):
    student_ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)


class InviteQueued(BaseModel):
    queued: int


class InviteRedeemOut(BaseModel):
    exam_id: uuid.UUID
    exam_title: str
    pin: str
    pin_expires_at: datetime


class InvitePinLogin(BaseModel):
    exam_id: uuid.UUID
    pin: str = Field(min_length=4, max_length=8)


class PublishResult(BaseModel):
    exam_id: uuid.UUID
    status: ExamStatus
    total_questions: int
    total_marks: float
    warnings: list[str] = []


class LiveStudentRow(BaseModel):
    attempt_id: uuid.UUID | None = None
    student_id: str
    name: str
    attempt_status: AttemptStatus | None
    started_at: datetime | None
    seconds_remaining: int | None
    answered_count: int
    focus_loss_count: int
    submitted_at: datetime | None


class LiveMonitorOut(BaseModel):
    exam_id: uuid.UUID
    title: str
    not_started: int
    in_progress: int
    submitted: int
    total_students: int
    server_time: datetime
    rows: list[LiveStudentRow]


class QuestionStat(BaseModel):
    question_id: uuid.UUID
    type: QuestionType
    body_preview: str
    attempted: int
    correct: int
    accuracy: float


class AnalyticsOut(BaseModel):
    exam_id: uuid.UUID
    submissions: int
    average_score: float | None
    median_score: float | None
    highest_score: float | None
    lowest_score: float | None
    max_score: float | None
    score_buckets: dict[str, int]
    question_stats: list[QuestionStat]


# ------------------------------------------------------- student details (admin)


class AttemptListItem(BaseModel):
    attempt_id: uuid.UUID
    student_id: str
    student_name: str
    email: str | None
    cohort: str | None
    exam_id: uuid.UUID
    exam_title: str
    status: AttemptStatus
    total_score: float | None
    max_score: float | None
    percentage: float | None
    started_at: datetime | None
    submitted_at: datetime | None
    time_taken_minutes: float | None


class AttemptListPage(BaseModel):
    items: list[AttemptListItem]
    total: int
    page: int
    page_size: int


class SectionScore(BaseModel):
    section_id: uuid.UUID
    title: str
    score: float
    max_score: float


class AttemptOverview(BaseModel):
    attempt_id: uuid.UUID
    student_id: str
    student_name: str
    email: str | None
    cohort: str | None
    exam_id: uuid.UUID
    exam_title: str
    status: AttemptStatus
    total_score: float | None
    max_score: float | None
    percentage: float | None
    rank: int | None
    passed: bool | None
    time_taken_minutes: float | None
    started_at: datetime | None
    submitted_at: datetime | None
    focus_loss_count: int
    sections: list[SectionScore]


class DiContext(BaseModel):
    title: str
    passage_md: str | None
    image_url: str | None


class RubricCriterionOut(BaseModel):
    """One rubric line, paired with what the AI awarded for it. `awarded` is None
    when there is no AI evaluation yet (or it failed)."""

    key: str
    description: str
    max_marks: float
    awarded: float | None = None
    # The AI's reason for this criterion's score. None for evaluations produced
    # before per-criterion justifications existed, or when there is none yet.
    justification: str | None = None


class CodingSubmissionOut(ORMModel):
    """Admin-only. Never returned on any student-facing route — it carries the AI
    reasoning, the admin's comment, and evaluation metadata."""

    id: uuid.UUID
    attempt_id: uuid.UUID
    question_id: uuid.UUID
    language: str
    code_text: str
    submitted_at: datetime
    max_marks: float
    status: EvaluationStatus

    ai_score: float | None = None
    ai_rubric_scores: dict[str, float] | None = None
    ai_rubric_justifications: dict[str, str] | None = None
    ai_reasoning: str | None = None
    ai_strengths: list[str] | None = None
    ai_issues: list[str] | None = None
    ai_confidence: float | None = None
    ai_requires_manual_review: bool = False
    ai_model: str | None = None
    ai_evaluated_at: datetime | None = None
    ai_error: str | None = None

    final_score: float | None = None
    admin_comment: str | None = None
    finalized_at: datetime | None = None

    # Denormalized for the review screen.
    student_name: str = ""
    student_number: str = ""
    exam_title: str = ""
    question_body_md: str = ""
    statement_md: str = ""
    constraints_md: str | None = None
    rubric_max_marks: float = 0.0
    rubric: list[RubricCriterionOut] = []
    # Derived: low AI confidence or the model asking for a human look.
    manual_review_recommended: bool = False


class CodingSubmissionListItem(BaseModel):
    id: uuid.UUID
    attempt_id: uuid.UUID
    question_id: uuid.UUID
    student_name: str
    student_number: str
    exam_id: uuid.UUID
    exam_title: str
    language: str
    submitted_at: datetime
    status: EvaluationStatus
    # What this question is worth on the exam — the scale `final_score` uses.
    max_marks: float
    # ai_score is on the *rubric's* scale (rubric_max_marks), which is not
    # necessarily the question's. Kept separate rather than pre-scaled so the
    # recommendation always equals the sum of its own rubric breakdown.
    ai_score: float | None = None
    rubric_max_marks: float = 0.0
    final_score: float | None = None
    manual_review_recommended: bool = False


class CodingSubmissionPage(BaseModel):
    items: list[CodingSubmissionListItem]
    total: int
    page: int
    page_size: int


class CodingGradeRequest(BaseModel):
    """Bounds are re-checked against the submission's own max_marks in the route —
    the marks a question is worth aren't known at schema-validation time."""

    final_score: float = Field(ge=0)
    admin_comment: str | None = Field(default=None, max_length=4000)
    # False saves a draft (ADMIN_REVIEWED); True finalizes, which is what makes the
    # score count toward the exam total.
    finalize: bool = False


class CodingReview(BaseModel):
    """The coding block inside an attempt's question-by-question review."""

    submission_id: uuid.UUID | None = None
    language: str
    code_text: str
    status: EvaluationStatus | None = None
    ai_score: float | None = None
    final_score: float | None = None
    max_marks: float = 0.0
    manual_review_recommended: bool = False


class QuestionReview(BaseModel):
    question_id: uuid.UUID
    type: QuestionType
    order_index: int
    body_md: str
    marks: float
    negative_marks: float
    # "pending" is coding-only: a submission awaiting an admin's final score is
    # neither correct nor incorrect yet.
    status: Literal["correct", "incorrect", "skipped", "pending"]
    student_answer: str | None
    correct_answer: str | None
    marks_awarded: float
    explanation_md: str | None
    di_context: DiContext | None = None
    coding: CodingReview | None = None


# --------------------------------------------------------- reorder / question bank


class ReorderItem(BaseModel):
    kind: Literal["question", "di_group"]
    id: uuid.UUID


class SectionReorderRequest(BaseModel):
    items: list[ReorderItem]


class QuestionBankItem(BaseModel):
    question_id: uuid.UUID
    exam_id: uuid.UUID
    exam_title: str
    section_id: uuid.UUID
    section_title: str
    type: QuestionType
    body_preview: str
    marks: float | None
    tags: list[str]
    difficulty: str | None


class QuestionBankPage(BaseModel):
    items: list[QuestionBankItem]
    total: int


class QuestionCopyRequest(BaseModel):
    target_section_id: uuid.UUID


# --------------------------------------------------------------- activity / stats


class ActivityEvent(BaseModel):
    type: str
    timestamp: datetime
    label: str
    detail: str | None = None


class ActivityTimeline(BaseModel):
    events: list[ActivityEvent]


class OverviewStats(BaseModel):
    exams: int
    students: int
    attempts: int
    average_score: float | None
