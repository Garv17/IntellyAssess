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
| Code run (test-against-samples) | ~6 / exam | ~0.7 rps | Expensive; queued, not synchronous. |
| Login burst | 400 in ~120 s | ~3–20 rps peak | Short spike at exam open. |
| Admin monitoring | — | ~1 rps | Polled dashboard. |

**Total steady-state ≈ 63 rps**, above the stated 42 rps. Two design consequences:

1. **Auto-save must not hit Postgres per request.** It writes to Redis and is flushed to Postgres in batches. This turns ~40 write-rps into ~2 bulk-write-rps.
2. **Code execution must be asynchronous.** A synchronous compile+run (0.5–5 s, CPU-bound) would exhaust the web worker pool at even 2 rps. It goes to a Celery queue with a bounded concurrency, and the client polls for the result.

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
   │  Celery workers      │      │  Judge sandbox      │
   │  • flush answers     │─────►│  Docker, per-run:   │
   │  • grade submissions │      │  no net, 128MB,     │
   │  • judge code        │      │  1 CPU, 5s timeout, │
   │  • export XLSX       │      │  read-only rootfs   │
   └──────────────────────┘      └─────────────────────┘
```

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
         -- is_sample=true is visible to students ("Run"); the rest are hidden

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

JudgeRun(id, attempt_id, question_id, language, code_text,
         mode[sample|final], status[queued|running|done|error],
         passed INT, total INT, results JSONB, created_at)

AuditLog(id, actor_type, actor_id, action, target, meta JSONB, created_at)
```

**Key constraints doing real work:**

- `UNIQUE(exam_id, student_id)` on `ExamAttempt` is the *only* correct way to enforce one-attempt-per-student. Checking "does an attempt exist?" in application code races under a login burst; the DB constraint does not.
- `UNIQUE(attempt_id, question_id)` on `Answer` makes every save an idempotent `INSERT … ON CONFLICT DO UPDATE`. Retries and duplicate flushes are harmless.
- `question_order` is frozen into the attempt row at start. Randomization must be stable across refreshes, so it cannot be recomputed per request.

**Indexes:** `answer(attempt_id)`, `exam_attempt(exam_id, status)` (live monitoring), `question(section_id, order_index)`, `test_case(coding_problem_id, is_sample)`.

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

A Celery beat task runs every 5 seconds: pop the `dirty_attempts` set, read those hashes, and bulk-upsert into `Answer`. Worst-case data loss is one flush interval, and only if Redis dies — an acceptable trade for a 20× reduction in write load. Redis runs with AOF `everysec` persistence to bound that further.

The client debounces: MCQ selections save immediately (cheap, discrete), code editor keystrokes save 10 s after the last keypress or on blur/navigation.

### 4.2 Timer — server is the authority

The browser countdown is display only. `deadline_at` is set once, server-side, at attempt start (`started_at + duration_minutes`) and mirrored into Redis with a TTL. Every answer save and heartbeat validates against it and returns `seconds_remaining`, so client drift self-corrects each cycle. A tampered client clock changes nothing: writes past `deadline_at` are rejected with `410 Gone`.

**Auto-submit** has two independent triggers, because relying on the client alone loses students whose laptop dies:
1. Client-side, on countdown reaching zero — the fast, normal path.
2. Server-side sweeper (Celery beat, every 30 s) — finalizes any `in_progress` attempt past its deadline. This is the safety net and the reason no student can ever be left unsubmitted.

### 4.3 Resume on refresh

`GET /api/exam/state` returns everything needed to rebuild the UI: attempt status, `seconds_remaining`, frozen question order, and all saved answers (Redis buffer overlaid on the Postgres rows, buffer winning). Because no exam state lives in the browser beyond the JWT, a refresh, tab close, or device switch resumes exactly where the student left off.

### 4.4 Code execution

```
POST /api/exam/code/run   → enqueue JudgeRun(mode=sample) → 202 {run_id}
GET  /api/exam/code/run/{run_id} → poll status/results
```

Each run executes in a throwaway Docker container: no network, read-only root filesystem, 128 MB memory, 1 CPU, `--pids-limit`, non-root user, and a hard wall-clock timeout. Worker concurrency is capped (e.g. 8) so a burst of runs queues rather than starving the host — an exam is degraded by slow judging, but destroyed by an unresponsive API.

Students run only against sample test cases. Hidden tests are executed once at submission by the grading task, which prevents both answer-mining and a stampede of full test-suite runs during the exam.

### 4.5 Grading

MCQ and DI grade synchronously at submit (a dictionary lookup against `is_correct`, with per-section negative marking). Coding questions are graded by an async task against the full test-case set, weighted per case. `ExamAttempt.total_score` is written when all questions are resolved, so results export is a plain read.

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
POST   /api/exam/code/run                     → 202 {run_id}
GET    /api/exam/code/run/{run_id}
POST   /api/exam/heartbeat                    → {seconds_remaining}
POST   /api/exam/submit                       → submission confirmation + receipt
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
GET    /api/admin/exams/{id}/live             live monitor (5 s poll)
GET    /api/admin/exams/{id}/students         per-student status
GET    /api/admin/exams/{id}/results.xlsx     streamed export
GET    /api/admin/exams/{id}/analytics        score distribution, per-question stats
```

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
| Sandbox escape | No network, read-only rootfs, dropped capabilities, non-root, pids/memory/CPU caps, wall-clock kill. |
| Rate limiting | Nginx: 10 rps/IP general. Magic-link requests are additionally capped to 1/min/student via Redis, to protect the Brevo daily send quota. |
| Transport | TLS only; HSTS; `Secure`/`SameSite=Strict` refresh cookie. |

**On proctoring:** tab-switch and focus-loss events are logged as advisory signals for the admin dashboard, not enforced blocks. Browser-side lockdown is trivially bypassable, and hard-failing an exam on a spurious blur event is worse than the cheating it prevents. Treat these as flags for human review.

---

## 7. Failure modes

| Failure | Behaviour |
|---|---|
| Redis down | Answer saves fall back to direct Postgres upsert (slower, still correct). Timer falls back to `deadline_at` from Postgres. Exam continues. |
| Postgres primary down | Exam halts; students see a retry banner. Client keeps answers in memory and replays on reconnect. |
| Judge worker saturated | Runs queue; UI shows "queued, position N". MCQ/DI path unaffected. |
| API worker crash | Stateless — Nginx routes to remaining workers, client retries idempotently. |
| Student loses network | Client buffers locally, replays on reconnect; server timer keeps running (this is correct — the exam clock is wall-clock). |
| Client clock tampered | Irrelevant; server enforces the deadline. |

The design principle throughout: **degrade the expensive features, never the exam clock or answer durability.**

---

## 8. Deployment & capacity

- 4 uvicorn workers per API container, 2 containers behind Nginx. Each worker handles ~30 rps of the auto-save path comfortably; this is ~4× headroom on the 63 rps estimate.
- Postgres: `max_connections=200`, SQLAlchemy pool of 20/worker with `pool_pre_ping`. Read replica for analytics and export so a heavy report can't slow the exam.
- Celery: one queue for `default` (flush, sweep, grade, export), one for `judge` with concurrency 8 on a dedicated host with Docker socket access.
- Observability: structured JSON logs, `/health` and `/ready`, Prometheus metrics on auto-save p99, flush lag, judge queue depth, and active attempts. Flush lag and queue depth are the two numbers that predict an incident.

**Pre-exam checklist:** load-test at 1.5× expected concurrency, verify the auto-submit sweeper against a seeded overdue attempt, confirm Redis AOF is on, and take a Postgres snapshot immediately before the exam window opens.