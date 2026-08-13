/**
 * API client.
 *
 * Two things here matter more than the rest: the access token is refreshed
 * transparently on 401 (a student must never be logged out mid-exam by an
 * expiring token), and a 410 signals the server closed the exam window, which
 * the exam page turns into an immediate hand-off to the confirmation screen.
 */

const ACCESS_KEY = 'exam.access';
const REFRESH_KEY = 'exam.refresh';
const ROLE_KEY = 'exam.role';

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public payload?: unknown,
  ) {
    super(message);
  }
}

export const tokens = {
  get access() {
    return localStorage.getItem(ACCESS_KEY);
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  get role() {
    return localStorage.getItem(ROLE_KEY);
  },
  set(access: string, refresh: string, role: string) {
    localStorage.setItem(ACCESS_KEY, access);
    localStorage.setItem(REFRESH_KEY, refresh);
    localStorage.setItem(ROLE_KEY, role);
  },
  setAccess(access: string) {
    localStorage.setItem(ACCESS_KEY, access);
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(ROLE_KEY);
  },
};

let refreshInFlight: Promise<boolean> | null = null;

async function refreshAccessToken(): Promise<boolean> {
  // Collapse concurrent refreshes — during an exam a dozen requests can 401 at once.
  if (refreshInFlight) return refreshInFlight;
  const refresh = tokens.refresh;
  if (!refresh) return false;

  refreshInFlight = (async () => {
    try {
      const res = await fetch('/api/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      tokens.setAccess(data.access_token);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  raw?: boolean;
  formData?: FormData;
  retryOn401?: boolean;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, formData, retryOn401 = true } = options;
  const headers: Record<string, string> = {};
  const access = tokens.access;
  if (access) headers.Authorization = `Bearer ${access}`;
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  const res = await fetch(path, {
    method,
    headers,
    body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });

  if (res.status === 401 && retryOn401) {
    if (await refreshAccessToken()) {
      return request<T>(path, { ...options, retryOn401: false });
    }
    tokens.clear();
    throw new ApiError(401, 'Session expired. Please sign in again.');
  }

  // The server re-issues an attempt-bound token on exam start; adopt it so
  // subsequent auto-saves skip an attempt lookup on the server.
  const attemptToken = res.headers.get('X-Attempt-Token');
  if (attemptToken) tokens.setAccess(attemptToken);

  if (res.status === 204) return undefined as T;

  if (!res.ok) {
    let detail = res.statusText;
    let payload: unknown;
    try {
      payload = await res.json();
      const d = (payload as { detail?: unknown }).detail;
      detail = typeof d === 'string' ? d : JSON.stringify(d ?? payload);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail, payload);
  }

  return (await res.json()) as T;
}

export const api = {
  requestMagicLink: (email: string) =>
    request<MagicLinkSent>('/api/auth/student/magic-link/request', {
      method: 'POST',
      body: { email },
    }),
  verifyMagicLink: (token: string) =>
    request<TokenPair>('/api/auth/student/magic-link/verify', {
      method: 'POST',
      body: { token },
    }),
  adminLogin: (email: string, password: string) =>
    request<TokenPair>('/api/auth/admin/login', { method: 'POST', body: { email, password } }),
  me: () => request<Me>('/api/auth/me'),
  logout: () => request<void>('/api/auth/logout', { method: 'POST' }),

  availableExams: () => request<ExamSummary[]>('/api/exam/available'),
  startExam: (examId: string) =>
    request<ExamState>(`/api/exam/${examId}/start`, { method: 'POST' }),
  examState: () => request<ExamState>('/api/exam/state'),
  examQuestions: () => request<ExamPaper>('/api/exam/questions'),
  saveAnswers: (answers: AnswerSave[]) =>
    request<SaveAck>('/api/exam/answers', { method: 'PATCH', body: { answers } }),
  heartbeat: (focusLost = false) =>
    request<Heartbeat>(`/api/exam/heartbeat?focus_lost=${focusLost}`, { method: 'POST' }),
  // There is deliberately no runCode/codeRun here: coding questions are
  // submission-only, and the server has no execution endpoint to call.
  submit: (auto = false) =>
    request<Receipt>(`/api/exam/submit?auto=${auto}`, { method: 'POST' }),

  adminExams: () => request<Exam[]>('/api/admin/exams'),
  createExam: (payload: Partial<Exam>) =>
    request<Exam>('/api/admin/exams', { method: 'POST', body: payload }),
  updateExam: (examId: string, payload: Partial<Exam>) =>
    request<Exam>(`/api/admin/exams/${examId}`, { method: 'PATCH', body: payload }),
  deleteExam: (examId: string) =>
    request<void>(`/api/admin/exams/${examId}`, { method: 'DELETE' }),
  uploadSebConfig: (examId: string, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<{ seb_url: string }>(`/api/admin/exams/${examId}/seb-config`, {
      method: 'POST',
      formData: fd,
    });
  },
  sections: (examId: string) => request<Section[]>(`/api/admin/exams/${examId}/sections`),
  createSection: (examId: string, payload: unknown) =>
    request<Section>(`/api/admin/exams/${examId}/sections`, { method: 'POST', body: payload }),
  createMcq: (sectionId: string, payload: unknown) =>
    request<unknown>(`/api/admin/sections/${sectionId}/questions`, { method: 'POST', body: payload }),
  createDiGroup: (sectionId: string, payload: unknown) =>
    request<unknown>(`/api/admin/sections/${sectionId}/di-groups`, { method: 'POST', body: payload }),
  createCoding: (sectionId: string, payload: unknown) =>
    request<unknown>(`/api/admin/sections/${sectionId}/coding`, { method: 'POST', body: payload }),
  sectionQuestions: (sectionId: string) =>
    request<QuestionDetail[]>(`/api/admin/sections/${sectionId}/questions`),
  sectionDiGroups: (sectionId: string) =>
    request<DIGroupDetail[]>(`/api/admin/sections/${sectionId}/di-groups`),
  updateMcq: (questionId: string, payload: unknown) =>
    request<QuestionDetail>(`/api/admin/questions/${questionId}/mcq`, {
      method: 'PATCH',
      body: payload,
    }),
  updateCoding: (questionId: string, payload: unknown) =>
    request<QuestionDetail>(`/api/admin/questions/${questionId}/coding`, {
      method: 'PATCH',
      body: payload,
    }),
  previewBoilerplate: (
    language: string,
    functionName: string,
    returnType: string,
    parameters: { name: string; type: string }[],
  ) =>
    request<{ code: string }>('/api/admin/coding/preview-boilerplate', {
      method: 'POST',
      body: { language, function_name: functionName, return_type: returnType, parameters },
    }),
  updateDiGroup: (groupId: string, payload: unknown) =>
    request<DIGroupDetail>(`/api/admin/di-groups/${groupId}`, { method: 'PATCH', body: payload }),
  deleteQuestion: (questionId: string) =>
    request<void>(`/api/admin/questions/${questionId}`, { method: 'DELETE' }),
  deleteDiGroup: (groupId: string) =>
    request<void>(`/api/admin/di-groups/${groupId}`, { method: 'DELETE' }),
  uploadImage: (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<{ image_url: string }>('/api/admin/uploads/image', {
      method: 'POST',
      formData: fd,
    });
  },
  bulkQuestions: (sectionId: string, file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<BulkResult>(`/api/admin/sections/${sectionId}/questions/bulk`, {
      method: 'POST',
      formData: fd,
    });
  },
  createStudent: (payload: { student_id: string; name: string; email: string; cohort?: string | null }) =>
    request<Student>('/api/admin/students', { method: 'POST', body: payload }),
  bulkStudents: (file: File) => {
    const fd = new FormData();
    fd.append('file', file);
    return request<BulkResult>('/api/admin/students/bulk', { method: 'POST', formData: fd });
  },
  students: (cohort?: string) =>
    request<Student[]>(
      `/api/admin/students?limit=500${cohort ? `&cohort=${encodeURIComponent(cohort)}` : ''}`,
    ),
  updateStudent: (studentId: string, payload: Partial<Student>) =>
    request<Student>(`/api/admin/students/${studentId}`, { method: 'PATCH', body: payload }),
  deleteStudent: (studentId: string) =>
    request<void>(`/api/admin/students/${studentId}`, { method: 'DELETE' }),
  sendStudentMagicLink: (studentId: string) =>
    request<MagicLinkSent>(`/api/admin/students/${studentId}/send-magic-link`, {
      method: 'POST',
    }),
  sendBulkMagicLinks: (studentIds: string[]) =>
    request<BulkMagicLinkQueued>('/api/admin/students/magic-link/bulk', {
      method: 'POST',
      body: { student_ids: studentIds },
    }),
  sendSebInvites: (examId: string, studentIds: string[]) =>
    request<{ queued: number }>(`/api/admin/exams/${examId}/invites`, {
      method: 'POST',
      body: { student_ids: studentIds },
    }),
  redeemInvite: (token: string) =>
    request<InviteRedeem>(`/api/invite/redeem?token=${encodeURIComponent(token)}`),
  pinLogin: (examId: string, pin: string) =>
    request<TokenPair>('/api/invite/pin-login', {
      method: 'POST',
      body: { exam_id: examId, pin },
    }),
  publish: (examId: string) =>
    request<PublishResult>(`/api/admin/exams/${examId}/publish`, { method: 'POST' }),
  liveMonitor: (examId: string) => request<LiveMonitor>(`/api/admin/exams/${examId}/live`),
  analytics: (examId: string) => request<Analytics>(`/api/admin/exams/${examId}/analytics`),
  forceSubmit: (attemptId: string) =>
    request<unknown>(`/api/admin/attempts/${attemptId}/force-submit`, { method: 'POST' }),
  resultsUrl: (examId: string) => `/api/admin/exams/${examId}/results.xlsx`,

  attempts: (filters: AttemptFilters = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '') params.set(key, String(value));
    }
    const qs = params.toString();
    return request<AttemptListPage>(`/api/admin/attempts${qs ? `?${qs}` : ''}`);
  },
  attemptsExportUrl: (filters: Omit<AttemptFilters, 'page' | 'page_size'> = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '') params.set(key, String(value));
    }
    const qs = params.toString();
    return `/api/admin/attempts/export.xlsx${qs ? `?${qs}` : ''}`;
  },
  attemptOverview: (attemptId: string) =>
    request<AttemptOverview>(`/api/admin/attempts/${attemptId}/overview`),
  attemptQuestions: (attemptId: string) =>
    request<QuestionReview[]>(`/api/admin/attempts/${attemptId}/questions`),
  attemptActivity: (attemptId: string) =>
    request<ActivityTimeline>(`/api/admin/attempts/${attemptId}/activity`),

  overviewStats: () => request<OverviewStats>('/api/admin/overview-stats'),
  reorderSection: (sectionId: string, items: ReorderItem[]) =>
    request<{ status: string }>(`/api/admin/sections/${sectionId}/reorder`, {
      method: 'PATCH',
      body: { items },
    }),
  questionBank: (filters: QuestionBankFilters = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '') params.set(key, String(value));
    }
    const qs = params.toString();
    return request<QuestionBankPage>(`/api/admin/question-bank${qs ? `?${qs}` : ''}`);
  },
  copyQuestion: (questionId: string, targetSectionId: string) =>
    request<QuestionAdminSummary>(`/api/admin/questions/${questionId}/copy`, {
      method: 'POST',
      body: { target_section_id: targetSectionId },
    }),

  codingSubmissions: (filters: CodingSubmissionFilters = {}) => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== '' && value !== false) params.set(key, String(value));
    }
    const qs = params.toString();
    return request<CodingSubmissionPage>(`/api/admin/coding-submissions${qs ? `?${qs}` : ''}`);
  },
  codingSubmission: (submissionId: string) =>
    request<CodingSubmissionDetail>(`/api/admin/coding-submissions/${submissionId}`),
  gradeCodingSubmission: (
    submissionId: string,
    payload: { final_score: number; admin_comment?: string | null; finalize: boolean },
  ) =>
    request<CodingSubmissionDetail>(`/api/admin/coding-submissions/${submissionId}`, {
      method: 'PATCH',
      body: payload,
    }),
  reevaluateCodingSubmission: (submissionId: string) =>
    request<CodingSubmissionDetail>(`/api/admin/coding-submissions/${submissionId}/re-evaluate`, {
      method: 'POST',
    }),
};

/** Downloads a protected file by fetching it with the auth header, then saving the blob. */
export async function downloadResults(examId: string, filename = 'results.xlsx') {
  const res = await fetch(api.resultsUrl(examId), {
    headers: { Authorization: `Bearer ${tokens.access}` },
  });
  if (!res.ok) throw new ApiError(res.status, 'Export failed');
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

/** Downloads the currently filtered Student Details result set as an Excel file. */
export async function downloadAttemptsExport(
  filters: Omit<AttemptFilters, 'page' | 'page_size'> = {},
  filename = 'attempts_export.xlsx',
) {
  const res = await fetch(api.attemptsExportUrl(filters), {
    headers: { Authorization: `Bearer ${tokens.access}` },
  });
  if (!res.ok) throw new ApiError(res.status, 'Export failed');
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

// ------------------------------------------------------------------- types

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}
export interface Me {
  id: string;
  name: string;
  role: string;
  identifier: string;
}
export interface ExamSummary {
  id: string;
  title: string;
  description: string | null;
  duration_minutes: number;
  starts_at: string | null;
  ends_at: string | null;
  status: string;
  attempt_status: string | null;
  requires_seb: boolean;
}
export interface AnswerSave {
  question_id: string;
  selected_option_id?: string | null;
  code_text?: string | null;
  language?: string | null;
  is_marked_for_review?: boolean;
}
export interface ExamState {
  attempt_id: string;
  exam_id: string;
  status: string;
  started_at: string;
  deadline_at: string;
  seconds_remaining: number;
  question_order: unknown;
  answers: AnswerSave[];
}
export interface Option {
  id: string;
  body: string;
}
export interface ParamDef {
  name: string;
  type: string;
}
export interface TestCase {
  id: string;
  stdin: string;
  expected_stdout: string;
  param_values: unknown[] | null;
  expected_value: unknown;
  explanation: string | null;
}
export interface CodingProblem {
  id: string;
  statement_md: string;
  constraints_md: string | null;
  allowed_languages: string[];
  time_limit_ms: number;
  memory_limit_mb: number;
  starter_code: Record<string, string>;
  problem_type: 'stdio' | 'function';
  function_name: string | null;
  return_type: string | null;
  parameters: ParamDef[] | null;
  sql_dialect: string | null;
  sql_schema_sql: string | null;
  sql_result_columns: string[] | null;
  sample_test_cases: TestCase[];
}
export interface DIGroup {
  id: string;
  title: string;
  passage_md: string | null;
  image_url: string | null;
}
export interface Question {
  id: string;
  type: 'mcq' | 'coding' | 'di';
  body_md: string;
  marks: number;
  negative_marks: number;
  di_group_id: string | null;
  options: Option[];
  coding_problem: CodingProblem | null;
}
export interface Section {
  id: string;
  exam_id?: string;
  title: string;
  instructions: string | null;
  order_index: number;
  questions: Question[];
  di_groups: DIGroup[];
  question_count?: number;
  marks_per_question?: number;
  negative_marks?: number;
}
export interface ExamPaper {
  exam_id: string;
  title: string;
  instructions_md: string | null;
  duration_minutes: number;
  sections: Section[];
}

// ---------------------------------------------------- admin edit/delete views
// Unlike Option/TestCase above (the student-facing shapes), these carry
// is_correct / is_sample so the exam builder can show and change the answer key.

export interface OptionAdmin {
  id: string;
  body: string;
  is_correct: boolean;
  order_index: number;
}
export interface TestCaseAdmin {
  id: string;
  stdin: string;
  expected_stdout: string;
  param_values: unknown[] | null;
  expected_value: unknown;
  explanation: string | null;
  is_sample: boolean;
  weight: number;
  order_index: number;
}
export interface CodingProblemAdmin {
  id: string;
  statement_md: string;
  constraints_md: string | null;
  allowed_languages: string[];
  time_limit_ms: number;
  memory_limit_mb: number;
  starter_code: Record<string, string>;
  problem_type: 'stdio' | 'function';
  function_name: string | null;
  return_type: string | null;
  parameters: ParamDef[] | null;
  sql_dialect: string | null;
  sql_schema_sql: string | null;
  sql_result_columns: string[] | null;
  test_cases: TestCaseAdmin[];
}
export type Difficulty = 'easy' | 'medium' | 'hard';

export interface QuestionDetail {
  id: string;
  section_id: string;
  type: 'mcq' | 'coding' | 'di';
  body_md: string;
  explanation_md: string | null;
  marks: number | null;
  negative_marks: number | null;
  order_index: number;
  di_group_id: string | null;
  options: OptionAdmin[];
  coding_problem: CodingProblemAdmin | null;
  tags: string[];
  difficulty: Difficulty | null;
}

export interface QuestionAdminSummary {
  id: string;
  section_id: string;
  type: 'mcq' | 'coding' | 'di';
  body_md: string;
  explanation_md: string | null;
  marks: number | null;
  order_index: number;
  di_group_id: string | null;
  tags: string[];
  difficulty: Difficulty | null;
}
export interface DIGroupDetail {
  id: string;
  section_id: string;
  title: string;
  passage_md: string | null;
  image_url: string | null;
  order_index: number;
  questions: QuestionDetail[];
}
export interface SaveAck {
  saved: number;
  seconds_remaining: number;
  server_time: string;
}
export interface Heartbeat {
  seconds_remaining: number;
  status: string;
  server_time: string;
}
export interface Receipt {
  attempt_id: string;
  status: string;
  submitted_at: string;
  answered_count: number;
  total_questions: number;
  receipt_code: string;
  grading_pending: boolean;
}
export interface Exam {
  id: string;
  title: string;
  description: string | null;
  instructions_md: string | null;
  duration_minutes: number;
  status: string;
  starts_at: string | null;
  ends_at: string | null;
  randomize_questions: boolean;
  randomize_options: boolean;
  cohort: string | null;
  pass_percentage: number | null;
  created_at: string;
  requires_seb: boolean;
  seb_config_key: string | null;
}
export interface MagicLinkSent {
  message: string;
  dev_token?: string | null;
}
export interface BulkMagicLinkQueued {
  queued: number;
}
export interface InviteRedeem {
  exam_id: string;
  exam_title: string;
  pin: string;
  pin_expires_at: string;
}
export interface BulkResult {
  created: number;
  skipped: number;
  errors: string[];
}
export interface Student {
  id: string;
  student_id: string;
  name: string;
  email: string;
  cohort: string | null;
  is_active: boolean;
}
export interface PublishResult {
  exam_id: string;
  status: string;
  total_questions: number;
  total_marks: number;
  warnings: string[];
}
export interface LiveRow {
  attempt_id: string | null;
  student_id: string;
  name: string;
  attempt_status: string | null;
  started_at: string | null;
  seconds_remaining: number | null;
  answered_count: number;
  focus_loss_count: number;
  submitted_at: string | null;
}
export interface LiveMonitor {
  exam_id: string;
  title: string;
  not_started: number;
  in_progress: number;
  submitted: number;
  total_students: number;
  server_time: string;
  rows: LiveRow[];
}
export interface Analytics {
  exam_id: string;
  submissions: number;
  average_score: number | null;
  median_score: number | null;
  highest_score: number | null;
  lowest_score: number | null;
  max_score: number | null;
  score_buckets: Record<string, number>;
  question_stats: {
    question_id: string;
    type: string;
    body_preview: string;
    attempted: number;
    correct: number;
    accuracy: number;
  }[];
}

// -------------------------------------------------------- student details

export interface AttemptListItem {
  attempt_id: string;
  student_id: string;
  student_name: string;
  email: string | null;
  cohort: string | null;
  exam_id: string;
  exam_title: string;
  status: string;
  total_score: number | null;
  max_score: number | null;
  percentage: number | null;
  started_at: string | null;
  submitted_at: string | null;
  time_taken_minutes: number | null;
}
export interface AttemptListPage {
  items: AttemptListItem[];
  total: number;
  page: number;
  page_size: number;
}
export interface AttemptFilters {
  search?: string;
  cohort?: string;
  exam_id?: string;
  status?: 'completed' | 'pending';
  min_score?: number;
  max_score?: number;
  date_from?: string;
  date_to?: string;
  sort?: string;
  order?: 'asc' | 'desc';
  page?: number;
  page_size?: number;
}
export interface SectionScore {
  section_id: string;
  title: string;
  score: number;
  max_score: number;
}
export interface AttemptOverview {
  attempt_id: string;
  student_id: string;
  student_name: string;
  email: string | null;
  cohort: string | null;
  exam_id: string;
  exam_title: string;
  status: string;
  total_score: number | null;
  max_score: number | null;
  percentage: number | null;
  rank: number | null;
  passed: boolean | null;
  time_taken_minutes: number | null;
  started_at: string | null;
  submitted_at: string | null;
  focus_loss_count: number;
  sections: SectionScore[];
}
export interface DiContext {
  title: string;
  passage_md: string | null;
  image_url: string | null;
}
export type EvaluationStatus =
  | 'pending'
  | 'ai_evaluating'
  | 'ai_evaluated'
  | 'ai_failed'
  | 'admin_reviewed'
  | 'finalized';

export interface CodingReview {
  submission_id: string | null;
  language: string;
  code_text: string;
  status: EvaluationStatus | null;
  ai_score: number | null;
  final_score: number | null;
  max_marks: number;
  manual_review_recommended: boolean;
}

// -------------------------------------------------------- coding evaluation

export interface CodingSubmissionFilters {
  exam_id?: string;
  status?: EvaluationStatus;
  needs_review?: boolean;
  search?: string;
  page?: number;
  page_size?: number;
}
export interface CodingSubmissionListItem {
  id: string;
  attempt_id: string;
  question_id: string;
  student_name: string;
  student_number: string;
  exam_id: string;
  exam_title: string;
  language: string;
  submitted_at: string;
  status: EvaluationStatus;
  // What the question is worth (the final_score scale).
  max_marks: number;
  // ai_score is on the rubric's scale, which need not equal max_marks.
  ai_score: number | null;
  rubric_max_marks: number;
  final_score: number | null;
  manual_review_recommended: boolean;
}
export interface CodingSubmissionPage {
  items: CodingSubmissionListItem[];
  total: number;
  page: number;
  page_size: number;
}
export interface RubricCriterion {
  key: string;
  description: string;
  max_marks: number;
  awarded: number | null;
  // Why this criterion scored what it did. Null on evaluations recorded before
  // per-criterion justifications existed.
  justification: string | null;
}
export interface CodingSubmissionDetail {
  id: string;
  attempt_id: string;
  question_id: string;
  language: string;
  code_text: string;
  submitted_at: string;
  max_marks: number;
  status: EvaluationStatus;

  ai_score: number | null;
  ai_rubric_scores: Record<string, number> | null;
  ai_rubric_justifications: Record<string, string> | null;
  ai_reasoning: string | null;
  ai_strengths: string[] | null;
  ai_issues: string[] | null;
  ai_confidence: number | null;
  ai_requires_manual_review: boolean;
  ai_model: string | null;
  ai_evaluated_at: string | null;
  ai_error: string | null;

  final_score: number | null;
  admin_comment: string | null;
  finalized_at: string | null;

  student_name: string;
  student_number: string;
  exam_title: string;
  question_body_md: string;
  statement_md: string;
  constraints_md: string | null;
  rubric_max_marks: number;
  rubric: RubricCriterion[];
  manual_review_recommended: boolean;
}
export interface QuestionReview {
  question_id: string;
  type: string;
  order_index: number;
  body_md: string;
  marks: number;
  negative_marks: number;
  // 'pending' is coding-only: awaiting an admin's final score.
  status: 'correct' | 'incorrect' | 'skipped' | 'pending';
  student_answer: string | null;
  correct_answer: string | null;
  marks_awarded: number;
  explanation_md: string | null;
  di_context: DiContext | null;
  coding: CodingReview | null;
}

// ---------------------------------------------------------- builder extras

export interface ReorderItem {
  kind: 'question' | 'di_group';
  id: string;
}

export interface QuestionBankFilters {
  search?: string;
  type?: 'mcq' | 'coding' | 'di';
  difficulty?: Difficulty;
  page?: number;
  page_size?: number;
}

export interface QuestionBankItem {
  question_id: string;
  exam_id: string;
  exam_title: string;
  section_id: string;
  section_title: string;
  type: 'mcq' | 'coding' | 'di';
  body_preview: string;
  marks: number | null;
  tags: string[];
  difficulty: Difficulty | null;
}

export interface QuestionBankPage {
  items: QuestionBankItem[];
  total: number;
}

// ------------------------------------------------------------ activity/stats

export interface ActivityEvent {
  type: 'started' | 'answered' | 'code_run' | 'tab_switch' | 'submitted' | string;
  timestamp: string;
  label: string;
  detail: string | null;
}

export interface ActivityTimeline {
  events: ActivityEvent[];
}

export interface OverviewStats {
  exams: number;
  students: number;
  attempts: number;
  average_score: number | null;
}
