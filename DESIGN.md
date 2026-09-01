# Online Examination Platform — System Design

**Target load:** 300–400 concurrent students, ~2,500 req/min (~42 rps) sustained, 60-minute exam windows.
**Stack:** Python 3.11 / FastAPI · PostgreSQL 15 · Redis 7 · Celery · React 18 (Vite) · Docker.

---

## 1. Load budget — what 42 rps actually means

Sizing the design starts with where the requests come from, because the naive assumption ("students click things") is wrong. Almost all traffic is machine-generated auto-save.

| Source | Rate per student | 400 students | Notes |
|---|---|---|---|
| Auto-save answer | 1 / 10 s (debounced) | ~40 rps | **Dominant load.** Write-heavy, tiny payloads. |
| Timer sync / heartbeat | 1 / 30 s | ~13 rps | Server is the clock authority. |
| Question navigation | 1 / 45 s | ~9 rps | Read-only, cacheable. |
| Coding submission | 1 / exam | ~0 rps | Not a request pattern at all — it rides the one `/submit` call. Evaluation happens after, off the request path. |
| Login burst | 400 in ~120 s | ~3–20 rps peak | Short spike at exam open; can be far higher against a single IP if many students share a school/office NAT (see §6 rate limiting). |
| Admin monitoring | — | ~1 rps | Polled dashboard. |

**Total steady-state ≈ 62 rps**, above the stated 42 rps. Two design consequences:

1. **Auto-save must not hit Postgres per request.** It writes to Redis and is flushed to Postgres in batches. This turns ~40 write-rps into ~2 bulk-write-rps.
2. **Nothing expensive may happen inside a request.** No student request compiles or runs code — there is no execution path at all — and the one genuinely slow operation left, an LLM call to evaluate a coding submission, is dispatched to a Celery queue after the submission row is committed. A request never waits on it, and the client never polls for it: the result is an admin-facing artifact, not something the student sees.

With those two moves, the Postgres-facing load during an exam is roughly 5–8 rps — comfortable for a single primary instance with a read replica for admin analytics.

---

## 2. Architecture

```
                    ┌──────────────┐
   Students ───────►│    Nginx     │  TLS, rate limit, static assets
   Admins  ───────►│ (reverse pxy) │
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │  FastAPI (4 × uvicorn)  │  stateless — scale horizontally
              └───┬──────────┬──────────┘
                  │          │
        ┌─────────▼──┐   ┌───▼──────────────┐
        │   Redis    │   │   PostgreSQL     │
        │            │   │                  │
        │ • answer   │   │ • exams,         │
        │   buffer   │   │   questions,     │
        │ • session/ │   │   students,      │
        │   timer    │   │   submissions    │
        │ • jti      │   │ • final answers  │
        │   denylist │   └──────────────────┘
        │ • celery   │
        │   broker   │
        └─────┬──────┘
              │
   ┌──────────▼───────────┐      ┌─────────────────────┐
   │ Celery `default` (2) │      │ Celery `ai` (4)     │
   │  • flush answers     │      │  • evaluate coding  │
   │  • auto-submit sweep │      │    submissions      │
   │  • grade MCQ/DI      │      └──────────┬──────────┘
   │  • send magic links  │                 │ HTTPS
   │  • export XLSX       │      ┌──────────▼──────────┐
   └──────────────────────┘      │  OpenAI API         │
                                 │  (gpt-4o-mini)      │
                                 └─────────────────────┘
```

**Why two queues:** an evaluation blocks a worker slot for seconds on a network call it
doesn't control. The answer flush and the auto-submit sweeper are exam-critical and run on
a fixed schedule; they must never queue behind a burst of submissions or an OpenAI
slowdown. Splitting the queues also makes the `ai` worker independently disposable — stop
it and exams still run, submissions still save, and an admin grades by hand.

**No sandbox, no Docker socket.** Student code is never executed anywhere in this system,
so no component needs the Docker daemon and no host runs untrusted programs.

**Why the API is stateless:** any uvicorn worker can serve any request because all mutable exam state (timer deadline, buffered answers, attempt status) lives in Redis. This is what makes the resume-on-refresh requirement cheap and horizontal scaling possible.

---

## 3. Data model

```
Admin(id, email, password_hash, role, created_at)

Student(id, student_id UNIQUE, name, email UNIQUE, cohort, created_at)

Exam(id, title, description, duration_minutes, status[draft|published|closed],
     starts_at, ends_at, randomize_questions, randomize_options,
     max_attempts=1, created_by → Admin, created_at)

Section(id, exam_id → Exam, title, order_index, marks_per_question,
        negative_marks, instructions)

Question(id, section_id → Section, type[mcq|coding|di],
         body_md, marks, negative_marks, order_index,
         di_group_id → DIGroup NULL,      -- set for type=di
         meta JSONB)

MCQOption(id, question_id → Question, body, is_correct, order_index)

DIGroup(id, section_id → Section, title, passage_md, image_url, order_index)
        -- one image/passage, many child Questions

CodingProblem(id, question_id → Question UNIQUE, statement_md,
              allowed_languages TEXT[], time_limit_ms, memory_limit_mb,
              starter_code JSONB)          -- {language: template}

TestCase(id, coding_problem_id → CodingProblem, stdin, expected_stdout,
         is_sample BOOL, weight INT)
         -- never executed: is_sample=true is shown to the student as a worked
         -- example, the rest are hidden and become expected-behaviour context
         -- for the evaluator

ExamAttempt(id, exam_id → Exam, student_id → Student,
            status[in_progress|submitted|auto_submitted|expired],
            started_at, deadline_at, submitted_at,
            question_order JSONB,          -- frozen per-student randomization
            total_score, max_score, ip_address, user_agent,
            UNIQUE(exam_id, student_id))   -- ← enforces one attempt per student

Answer(id, attempt_id → ExamAttempt, question_id → Question,
       selected_option_id NULL, code_text NULL, language NULL,
       is_correct NULL, score, updated_at,
       UNIQUE(attempt_id, question_id))    -- upsert target

CodingSubmission(id, attempt_id → ExamAttempt, question_id → Question,
         student_id → Student,            -- denormalized for the review list
         language, code_text, submitted_at, max_marks,
         status[pending|ai_evaluating|ai_evaluated|ai_failed|admin_reviewed|finalized],
         ai_score, ai_rubric_scores JSONB, ai_reasoning, ai_strengths JSONB,
         ai_issues JSONB, ai_confidence, ai_requires_manual_review BOOL,
         ai_model, ai_rubric_version, ai_evaluated_at, ai_error,
         final_score NULL, admin_id → Admin NULL, admin_comment, finalized_at,
         UNIQUE(attempt_id, question_id))  -- ← one submission, one evaluation

AuditLog(id, actor_type, actor_id, action, target, meta JSONB, created_at)
```

**Key constraints doing real work:**

- `UNIQUE(exam_id, student_id)` on `ExamAttempt` is the *only* correct way to enforce one-attempt-per-student. Checking "does an attempt exist?" in application code races under a login burst; the DB constraint does not.
- `UNIQUE(attempt_id, question_id)` on `Answer` makes every save an idempotent `INSERT … ON CONFLICT DO UPDATE`. Retries and duplicate flushes are harmless.
- `question_order` is frozen into the attempt row at start. Randomization must be stable across refreshes, so it cannot be recomputed per request.
- `UNIQUE(attempt_id, question_id)` on `CodingSubmission` does double duty: it makes the submit-time snapshot idempotent (`ON CONFLICT DO NOTHING`, so a retried submit inserts nothing and enqueues nothing), and it is the reason a duplicate task delivery can never produce a second evaluation of the same code.

**Why `CodingSubmission` is separate from `Answer`:** autosave keeps rewriting `Answer` right up to the deadline. The submission row is a snapshot taken at submit, so what was evaluated is exactly what was submitted and a late flush cannot mutate it underneath the evaluator. The `ai_*` columns and the admin's `final_score`/`admin_comment` are disjoint on purpose — an override never overwrites the recommendation, so the original stays auditable — and `max_marks` is frozen at submit so re-marking a question later can't silently invalidate a finalized score.

**Indexes:** `answer(attempt_id)`, `exam_attempt(exam_id, status)` (live monitoring), `question(section_id, order_index)`, `test_case(coding_problem_id, is_sample)`, `coding_submission(attempt_id)` and `coding_submission(status)` (the admin review queue filters on it).

---

## 4. Critical flows

### 4.1 Auto-save (the hot path)

The requirement "auto-save every answer" combined with 400 students is the platform's main scaling problem. The path is deliberately short:

```
PATCH /api/exam/answers        →  validate JWT (no DB hit — claims carry attempt_id)
                               →  check deadline from Redis (no DB hit)
                               →  HSET attempt:{id}:answers  {question_id: payload}
                               →  SADD dirty_attempts {attempt_id}
                               →  return 200  (p99 target < 30 ms)
```

A Celery beat task runs every `AUTOSAVE_FLUSH_SECONDS` (15 s by default): pop the `dirty_attempts` set (capped at 500 per run), read those hashes, and bulk-upsert into `Answer` in one query. This is a single scheduled job regardless of how many of the 400 students are dirty, not a per-student request, so shortening the interval doesn't add per-user load — it only bounds the data-loss window if Redis dies uncleanly. 15 s keeps that window small while still batching client auto-saves (1/10 s debounced) and the 10 s frontend safety-flush into a couple of bulk writes per second. Redis runs with AOF `everysec` persistence to bound the Redis-loss case further.

The client debounces: MCQ selections save immediately (cheap, discrete), code editor keystrokes save 10 s after the last keypress or on blur/navigation.

### 4.2 Timer — server is the authority

The browser countdown is display only. `deadline_at` is set once, server-side, at attempt start (`started_at + duration_minutes`) and mirrored into Redis with a TTL. Every answer save and heartbeat validates against it and returns `seconds_remaining`, so client drift self-corrects each cycle. A tampered client clock changes nothing: writes past `deadline_at` are rejected with `410 Gone`.

**Auto-submit** has two independent triggers, because relying on the client alone loses students whose laptop dies:
1. Client-side, on countdown reaching zero — the fast, normal path.
2. Server-side sweeper (Celery beat, every 30 s) — finalizes any `in_progress` attempt past its deadline. This is the safety net and the reason no student can ever be left unsubmitted.

### 4.3 Resume on refresh

`GET /api/exam/state` returns everything needed to rebuild the UI: attempt status, `seconds_remaining`, frozen question order, and all saved answers (Redis buffer overlaid on the Postgres rows, buffer winning). Because no exam state lives in the browser beyond the JWT, a refresh, tab close, or device switch resumes exactly where the student left off.

### 4.4 Coding submission and evaluation

There is no execution path. The student writes code, autosave persists it like any other answer, and the only action available is Submit.

```
POST /api/exam/submit  →  grade MCQ/DI synchronously
                       →  snapshot every non-empty coding answer into
                          CodingSubmission (ON CONFLICT DO NOTHING)
                       →  COMMIT
                       →  ai.evaluate_coding_submission.delay(id)  per new row
                       →  return receipt
```

The ordering is the whole design. The row is committed *before* the task is dispatched, for two reasons: the worker reads it over its own connection and would otherwise race the request's commit, and — more importantly — everything after the commit is best-effort. A dead broker, a stopped `ai` worker, a missing API key, an OpenAI outage, or a response that fails validation all leave a committed submission behind. **AI failure is never submission failure.**

The task itself claims the row with a conditional `UPDATE … WHERE status IN (pending, ai_failed)`, so two deliveries of the same id race and exactly one wins. `admin_reviewed` and `finalized` are deliberately not claimable: a duplicate or re-queued job must never overwrite a decision a human already made.

Evaluation renders the rubric (`assessments/rubrics/default_coding.yaml`), the problem statement, up to 8 test cases as expected-behaviour context, and the student's code into the evaluator prompt, and requests a strict JSON schema response. The model is treated as an untrusted source of numbers: a score above a rubric cap, a negative score, a missing criterion, or parts that don't sum to the stated total is rejected outright rather than clamped, landing on `AI_FAILED` for a human rather than becoming a plausible-looking mark. The rubric's 10 points are scaled to each question's own `max_marks`.

### 4.5 Grading

MCQ and DI grade synchronously at submit (a dictionary lookup against `is_correct`, with per-section negative marking). Coding questions contribute nothing at that point: `Answer.score` stays 0 and the attempt is marked grading-incomplete until an admin finalizes each submission, at which point the finalized score is folded into `ExamAttempt.total_score`. An AI recommendation is never automatically a mark — it exists to make the admin's review fast, not to replace it. Once every coding submission is finalized the attempt is complete and results export is a plain read.

---

## 5. API surface

**Auth**
```
POST   /api/auth/student/magic-link/request  {email}          → generic ack (emails a link)
POST   /api/auth/student/magic-link/verify   {token}          → {access, refresh}
POST   /api/auth/admin/login        {email, password}
POST   /api/auth/refresh
POST   /api/auth/logout             (jti → Redis denylist)
```

**Student**
```
GET    /api/exam/available                    exams open to this student
POST   /api/exam/{exam_id}/start              idempotent; creates or returns attempt
GET    /api/exam/state                        full resume payload
GET    /api/exam/questions                    sections + questions in frozen order
PATCH  /api/exam/answers                      auto-save (single or batch)
POST   /api/exam/heartbeat                    → {seconds_remaining}
POST   /api/exam/submit                       → receipt; snapshots coding answers
                                                and queues their evaluation
```

**Admin**
```
POST   /api/admin/exams                       create
POST   /api/admin/exams/{id}/sections
GET    /api/admin/sections/{id}/questions     standalone MCQ/coding questions, admin detail (options + is_correct)
POST   /api/admin/sections/{id}/questions     single
POST   /api/admin/sections/{id}/questions/bulk  CSV/XLSX upload
PATCH  /api/admin/questions/{id}/mcq          edit body/options/correct answer (MCQ or DI question)
PATCH  /api/admin/questions/{id}/coding       edit statement/languages/test cases
DELETE /api/admin/questions/{id}              remove a single question
GET    /api/admin/sections/{id}/di-groups     DI sets with nested questions, admin detail
POST   /api/admin/sections/{id}/di-groups     multipart: image + questions
PATCH  /api/admin/di-groups/{id}              edit stimulus (title/passage/image)
DELETE /api/admin/di-groups/{id}              remove a DI set and its questions
POST   /api/admin/sections/{id}/coding        problem + test cases
POST   /api/admin/students/bulk               CSV upload, generates credentials
POST   /api/admin/exams/{id}/publish          validates then flips to published
GET    /api/admin/exams/{id}/live             live monitor snapshot (REST fallback)
WS     /api/admin/exams/{id}/live/ws          live monitor push, Redis pub/sub fan-out across workers
GET    /api/admin/exams/{id}/students         per-student status
GET    /api/admin/exams/{id}/results.xlsx     streamed export
GET    /api/admin/exams/{id}/analytics        score distribution, per-question stats
GET    /api/admin/coding-submissions          review queue; filter by exam, status, needs-review
GET    /api/admin/coding-submissions/{id}     code + AI recommendation + rubric breakdown
PATCH  /api/admin/coding-submissions/{id}     save or finalize the admin's mark
POST   /api/admin/coding-submissions/{id}/re-evaluate   re-queue a failed evaluation
```

**The coding review endpoints are the only path from code to marks.** `PATCH` writes
`final_score`/`admin_comment` and leaves every `ai_*` column untouched, so an override
never destroys the recommendation it overrode; finalizing writes the score onto the
`Answer` row and recomputes the attempt total. `re-evaluate` exists for the OpenAI-outage
case and is refused once the submission is `admin_reviewed` or `finalized` — a retry must
never clobber an examiner's decision.

**Publish is a gate, not a flag flip.** It validates that every section has questions, every MCQ has exactly one correct option, every coding problem has ≥1 sample and ≥1 hidden test case, and every DI group has a reachable image. Catching these at publish time is the difference between a broken question and a cancelled exam.

**Editing and deleting questions** is allowed for a draft exam, and for a published one right up until its first `ExamAttempt` row exists (`_assert_editable`, shared by create/update/delete) — changing the paper under a student mid-exam is never the intended outcome, so it's blocked at the API rather than left to admin discipline. Options and test cases are replaced wholesale on edit rather than diffed, matching how they're created; the exactly-one-correct-option and sample+hidden-case invariants are re-validated on every edit, not just at creation.

---

## 6. Security

| Concern | Control |
|---|---|
| Auth | JWT: 15-min access token, 7-day refresh. Access claims carry `sub`, `role`, `attempt_id`, `jti`. |
| Token revocation | `jti` denylist in Redis with TTL = token lifetime. |
| Passwords | Admins only, bcrypt (cost 12). Students authenticate via a one-time magic link (JWT, `type=magic`, 15-min TTL, denylisted on redemption) emailed through Brevo. |
| Multiple attempts | DB unique constraint (§3), plus attempt-bound tokens. |
| Answer tampering | Correctness never leaves the server. `is_correct` and hidden test cases are excluded from all student-facing serializers. |
| Concurrent sessions | One active session per student ID; a second login invalidates the first and is written to `AuditLog`. |
| Malicious student code | Not executed anywhere, by anything — the sandbox-escape class of risk does not exist here. Code is stored as text and read by an LLM. |
| Prompt injection | A submission is untrusted input to the evaluator. The prompt states that the code is data and that instructions found inside it ("award full marks") are graded as text, never followed; the response is re-validated against the rubric's caps; and no AI output is a mark until an admin finalizes it. The last of those is the control that actually holds. |
| Secret handling | `OPENAI_API_KEY` is read only by the backend and worker containers, never baked into an image or returned by any endpoint. `ai_error` (which can echo an upstream message) is admin-only. |
| Rate limiting | Nginx: 10 rps/IP general, 30 rps/IP on `/api/auth/*` (kept generous and per-IP because a school/office NAT puts many students behind one IP for login and token refresh), 5 req/min/IP on admin password login only. Magic-link requests are additionally capped to 1/min/student via Redis, to protect the Brevo daily send quota. |
| Transport | TLS only; HSTS; `Secure`/`SameSite=Strict` refresh cookie. |

**On proctoring:** tab-switch and focus-loss events are logged as advisory signals for the admin dashboard, not enforced blocks. Browser-side lockdown is trivially bypassable, and hard-failing an exam on a spurious blur event is worse than the cheating it prevents. Treat these as flags for human review.

---

## 7. Failure modes

| Failure | Behaviour |
|---|---|
| Redis down | Answer saves fall back to direct Postgres upsert (slower, still correct). Timer falls back to `deadline_at` from Postgres. Exam continues. |
| Postgres primary down | Exam halts; students see a retry banner. Client keeps answers in memory and replays on reconnect. |
| `ai` worker saturated, stopped, or OpenAI down | Submissions are already committed; evaluations queue or land on `ai_failed`. Nothing the student does is affected — they submitted and left. Admins grade by hand or hit **Re-evaluate** once the model is reachable again. |
| API worker crash | Stateless — Nginx routes to remaining workers, client retries idempotently. |
| Student loses network | Client buffers locally, replays on reconnect; server timer keeps running (this is correct — the exam clock is wall-clock). |
| Client clock tampered | Irrelevant; server enforces the deadline. |

The design principle throughout: **degrade the expensive features, never the exam clock or answer durability.**

---

## 8. Deployment & capacity

- 4 uvicorn workers per API container, 2 containers behind Nginx. Each worker handles ~30 rps of the auto-save path comfortably; this is ~4× headroom on the 62 rps estimate.
- Postgres: `max_connections=200`, SQLAlchemy pool of 20/worker with `pool_pre_ping`. Read replica for analytics and export so a heavy report can't slow the exam.
- Celery: two queues. `default` (flush, sweep, magic-link sends, export) runs at concurrency 2 — it is latency-sensitive but cheap. `ai` (coding evaluation) runs at concurrency 4 in its own container: the work is I/O-bound on an HTTP call, not CPU-bound, and nothing there executes code, so neither worker needs a Docker socket or a privileged host. The `ai` worker can be stopped or scaled independently of the exam.
- Observability: structured JSON logs (§9), `/health` and `/ready`, Prometheus metrics on auto-save p99, flush lag, `ai` queue depth, and active attempts. Flush lag is the number that predicts an exam-day incident; `ai` queue depth predicts only how long admins wait to start grading.

**Pre-exam checklist:** load-test at 1.5× expected concurrency, verify the auto-submit sweeper against a seeded overdue attempt, confirm Redis AOF is on, confirm `OPENAI_API_KEY` is set (or accept that coding answers will be graded by hand), and take a Postgres snapshot immediately before the exam window opens.

---

## 9. Logging

One module owns logging: `backend/app/logging_config.py`. Nothing else calls
`logging.basicConfig`, and every module takes its logger from `get_logger(__name__)`.

**Two entry points configure it.** `app.main` at import, and the Celery
`after_setup_logger` / `after_setup_task_logger` signals in `app.tasks.celery_app`.
Both matter: workers never import `app.main`, so before the signals existed the
entire worker tier ran on Celery's default format while the web tier ran on ours.

**Format.** One JSON object per line, built with `json.dumps` — never string
interpolation into a JSON literal, which is how the previous format emitted
unparseable lines whenever a message contained a quote. `LOG_FORMAT=console`
switches to a readable single line for local work. Anything passed as
`extra={...}` becomes its own top-level field, so messages stay short and
constant and the data stays queryable.

**Correlation.** The HTTP middleware assigns each request an ID — honouring an
inbound `X-Request-ID` if a proxy set one — stores it in a ContextVar, and echoes
it on the response. ContextVars are per-task and survive `await`, so every line a
request emits carries the ID without any call site passing it, and `deps.py`
stamps the authenticated actor into the same context once the token is validated.
Celery's `task_prerun` puts the task ID in the same slot, so a coding submission
can be followed from the student's POST through to the AI evaluation minutes later.

**Levels.** INFO is the floor, in production as well as locally — nothing the
app emits is below it, because a line that is invisible in production is not
observability. INFO covers normal business progress: startup, logins, exam
started/submitted, task outcomes, one line per completed operation. WARNING is a caller fault or a degraded-but-working path —
a rejected credential, a 4xx, Redis unavailable so auto-save is hitting Postgres
directly. ERROR is an app fault that needs a human. Health probes (`/health`,
`/ready`, `/`) are not access-logged unless they fail; loops and per-row work log
an aggregate count, never a line per row.

**Volume control per module, not just globally.** `LOG_LEVEL` is a blunt
instrument — raising it to hide one noisy line hides every other module's INFO
along with it, including the ones an admin tailing logs during a live exam
actually wants (logins, submissions, auto-submits). Where one line is legitimately
high-volume, it gets its own settings flag instead, following the pattern
`LOG_SQL` set for SQLAlchemy echo: `LOG_LIVE_MONITOR` (default off) governs
`app.services.monitor`'s "live snapshot built" line, which fires on every
Live Monitor rebuild — every answer-flush cycle for every exam with activity —
and is the highest-volume line in the system on a busy exam day. It carries the
only record of snapshot latency, so it isn't deleted, just silenced by default;
flip the flag on specifically when "the dashboard is lagging" is the thing being
diagnosed.

**Secrets and PII never reach a sink.** The formatter redacts any field whose name
contains `password`, `token`, `secret`, `api_key`, `authorization`, `cookie`,
`pin`, `config_key`, `hash` and similar — recursively, depth-bounded. Emails go
through `mask_email()`. Secrets that genuinely need comparing across lines — the
SEB Config Key is the real case — go through `fingerprint()`, a truncated SHA-256
that is stable and comparable but not reversible. Answer content, student code,
JWTs, magic links and exam PINs are never logged in any form, at any level.
Because the redactor matches on substrings, a field holding an already-safe
fingerprint must be named to avoid them (`exam_key_fp`, not `config_key_fp`) —
`backend/tests/test_logging.py` locks all of this down.

**Frontend.** `frontend/src/logger.ts` mirrors the same redaction rules and level
gating in the browser, and an error boundary plus `window.onerror` /
`unhandledrejection` handlers mean a render crash mid-exam leaves a trace instead
of a white screen. It reads the `X-Request-ID` off each response, so a client-side
error can be joined to the server request that caused it.