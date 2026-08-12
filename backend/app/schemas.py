from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models import AttemptStatus, ExamStatus, JudgeStatus, QuestionType


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------- auth


class MagicLinkRequest(BaseModel):
    email: EmailStr


class MagicLinkVerify(BaseModel):
    token: str = Field(min_length=1)


class MagicLinkSent(BaseModel):
    message: str = "If that email is registered, a sign-in link is on its way."
    # Only ever populated when ENVIRONMENT=development, so loadtest.py can redeem a
    # link without a real mailbox. Never set outside dev — see request_magic_link.
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


# ------------------------------------------------------------------- judge


class CodeRunRequest(BaseModel):
    question_id: uuid.UUID
    language: str = Field(max_length=32)
    code_text: str = Field(max_length=65536)
    # Run against exactly one sample case (must have is_sample=True on this problem)...
    test_case_id: uuid.UUID | None = None
    # ...or ad-hoc input with no expected output to grade against. At most one of
    # these two should be set; if neither is set, every sample case runs (legacy
    # "run all samples" behavior).
    custom_stdin: str | None = Field(default=None, max_length=8192)
    # Function-mode equivalent of custom_stdin — ordered parameter values.
    custom_params: list[Any] | None = None


class CodeRunAccepted(BaseModel):
    run_id: uuid.UUID
    status: JudgeStatus


class TestCaseResult(BaseModel):
    index: int
    passed: bool
    stdin: str | None = None
    expected: str | None = None
    actual: str | None = None
    stderr: str | None = None
    time_ms: int | None = None
    verdict: str


class CodeRunOut(BaseModel):
    run_id: uuid.UUID
    status: JudgeStatus
    passed: int
    total: int
    results: list[TestCaseResult] = []
    error: str | None = None


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


# Must match the keys in app.services.sandbox.LANGUAGES — that's what actually
# knows how to compile/run each one.
CodingLanguage = Literal["python", "javascript", "c", "cpp", "java", "sql"]

# Display metadata only — every dialect still executes on the sqlite sandbox in
# app.services.sandbox; there's no per-dialect judge yet.
SqlDialect = Literal["sqlite", "mysql", "postgresql", "mssql"]

# Must match app.services.harness.PARAM_TYPES — that's what actually knows how to
# decode/encode each one, per language.
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


class SqlPreviewRequest(BaseModel):
    # Schema/seed SQL for one test case — same text an admin would put in that
    # case's `stdin`. Optional: a query with no setup (e.g. `SELECT 1;`) is valid.
    setup_sql: str = ""
    query_sql: str = Field(min_length=1)


class SqlPreviewOut(BaseModel):
    stdout: str
    error: str | None = None


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


class CodingCaseReview(BaseModel):
    index: int
    passed: bool
    stdin: str | None
    expected: str | None
    actual: str | None
    stderr: str | None
    time_ms: int | None
    verdict: str


class CodingReview(BaseModel):
    language: str
    code_text: str
    status: JudgeStatus
    passed: int
    total: int
    score: float
    cases: list[CodingCaseReview]


class QuestionReview(BaseModel):
    question_id: uuid.UUID
    type: QuestionType
    order_index: int
    body_md: str
    marks: float
    negative_marks: float
    status: Literal["correct", "incorrect", "skipped"]
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
