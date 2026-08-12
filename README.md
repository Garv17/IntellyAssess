# Online Examination Platform

Real-time examination platform for 300–400 concurrent students, supporting MCQ, Data
Interpretation, and sandboxed coding assessments.

Step-by-step setup: **[SETUP.md](SETUP.md)**.
Architecture, capacity model, and failure analysis: **[DESIGN.md](DESIGN.md)**.

**Stack:** FastAPI · PostgreSQL 15 · Redis 7 · Celery · React 18 + Vite · Docker.

---

## Quick start (Docker)

```bash
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))") \
  docker compose up --build -d

# create the schema and a demo exam + 25 students
docker compose exec backend python -m scripts.seed --students 25
```

| Service | URL |
|---|---|
| Student portal | http://localhost:5180 |
| Admin console | http://localhost:5180/admin/login |
| API docs | http://localhost:8000/docs |

Demo credentials (created by the seed script):

- **Admin**: `admin@example.com` / `Admin@123`
- **Student**: no password. Students sign in via a magic link emailed to their
  registered address (`STU0001` is `stu0001@example.com`). In development, no real
  mailbox is needed; see [SETUP.md](SETUP.md) step 7 for how to redeem a link locally.

## Local development

```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then edit JWT_SECRET
python -m scripts.seed
uvicorn app.main:app --reload

# background workers (separate terminals)
celery -A app.tasks.celery_app.celery_app worker -Q default -c 4 --loglevel=info
celery -A app.tasks.celery_app.celery_app worker -Q judge   -c 4 --loglevel=info
celery -A app.tasks.celery_app.celery_app beat --loglevel=info

# frontend
cd frontend
npm install
npm run dev
```

Postgres and Redis are still needed locally — `docker compose up -d postgres redis` is
enough.

### Database migrations

The seed script calls `create_all`, which is fine for development. For anything you
intend to keep, use Alembic:

```bash
cd backend
alembic revision --autogenerate -m "initial schema"
alembic upgrade head
```

---

## How the platform holds 400 students

Three decisions carry the load; the rest is ordinary CRUD.

**1. Auto-save never touches Postgres directly.** Answers land in a Redis hash and the
attempt is added to a dirty set; a Celery beat task bulk-upserts every `AUTOSAVE_FLUSH_SECONDS`
(15 s by default, capped at 500 attempts per run — one batched job, not one request per
student, so lowering it doesn't add per-user load). This
turns ~40 write-rps into a couple of bulk writes/second. If Redis is unavailable the API falls
back to a direct upsert — slower, still correct.

**2. The server owns the clock.** `deadline_at` is fixed at attempt start. The browser
countdown is display only and is corrected by every heartbeat, so a tampered client
clock, a suspended laptop, or a throttled tab changes nothing. Two independent
auto-submit paths exist: the client at zero, and a server-side sweeper every 30 s that
finalizes any overdue attempt. No student can be left unsubmitted.

**3. Code execution is queued, never synchronous.** Each run is a throwaway Docker
container with no network, a read-only rootfs, 128 MB, 1 CPU, dropped capabilities, and
a hard timeout. Judge workers have their own queue and bounded concurrency, so a burst
of submissions queues instead of starving the API.

Everything mutable during an exam lives in Redis, which is what makes the API stateless,
horizontally scalable, and resume-on-refresh trivial.

---

## Repository layout

```
backend/
  app/
    main.py            FastAPI app, middleware, health/readiness
    config.py          settings (env-driven)
    models.py          SQLAlchemy models + the constraints that enforce exam rules
    schemas.py         Pydantic I/O — no serializer exposes answer keys
    deps.py            auth dependencies, deadline enforcement
    cache.py           Redis: answer buffer, timer, token denylist
    security.py        JWT + bcrypt
    routers/
      auth.py          student & admin login, refresh, logout
      student.py       start, state, questions, auto-save, run code, submit
      admin.py         exam builder, uploads, publish gate, monitor, analytics, export
    services/
      paper.py         per-student randomization (frozen at start)
      answers.py       idempotent bulk upsert
      grading.py       MCQ/DI scoring with negative marking
      sandbox.py       Docker sandbox
      harness.py       function-mode driver generation (param encode/decode)
      harness_langs/   per-language boilerplate for function-mode problems
      monitor.py       live-monitor snapshot, shared by the poll and WebSocket paths
      email.py         Brevo transactional email (magic links)
      export.py        XLSX results
    tasks/
      maintenance.py   answer flush + auto-submit sweeper
      judge_tasks.py   sample runs and final grading
      mailer_tasks.py  bulk magic-link sends
    ws_manager.py      per-worker WebSocket registry for Live Monitor
    pubsub.py          Redis pub/sub fan-out across uvicorn workers
  scripts/
    seed.py            schema + demo exam + students
    loadtest.py        simulates N concurrent students
frontend/
  src/
    api.ts             typed client, transparent token refresh
    monaco.ts          local Monaco registration (no CDN — see below)
    hooks/
      useAutoSave.ts          batching debounced save with retry
      useExamTimer.ts         server-corrected countdown
      useLiveMonitorSocket.ts WebSocket client for the admin live monitor
    components/
      CodeEditor.tsx   lazy-loaded editor, keeps Monaco out of the initial bundle
    pages/             Login, MagicLinkCallback, Dashboard, Exam, Submitted, admin/*
```

The code editor is bundled and served from the same origin rather than fetched from a
CDN, because exam halls often sit behind a proxy allowlist and a failed CDN request
looks to the student like a broken exam. It is lazy-loaded, so the ~600 kB (gzipped)
editor chunk is only downloaded when a candidate opens a coding question.

---

## Admin workflow

1. **Create exam** — title, duration, cohort. Blank cohort = visible to all students.
2. **Add sections** — marks per question and negative marking are set per section.
3. **Add questions** — MCQ (single or CSV bulk), DI sets (image/table + child questions,
   created together), coding problems (statement, languages, sample + hidden test cases).
4. **Edit or delete questions** — the "Existing questions" list under each section lets you
   change a question's wording, options, correct answer, explanation, marks, or (for coding)
   statement/languages/test cases, or delete it outright. DI sets can be edited (title/passage/
   image) or deleted as a whole, and their child questions individually — same as any MCQ.
   This works both before and after publishing; it's only blocked once the first student
   attempt exists, so a live paper can't shift under someone mid-exam.
5. **Upload students** — CSV; blank passwords are generated and returned once. Download
   that CSV before leaving the page; the passwords are not recoverable.
6. **Publish** — validated, not a flag flip. Blocks on missing questions, MCQs without
   exactly one correct option, coding problems without both sample and hidden cases, DI
   groups with a missing image, and a window shorter than the duration.
7. **Monitor** — live counts and per-student rows, pushed over WebSocket the moment
   something changes, with a 5 s poll as a fallback if the socket drops. Force-submit
   and time-extension are available per attempt and both are audit-logged.
8. **Export / analytics** — XLSX results, score distribution, per-question accuracy.

### CSV formats

**Students** — `enrollment_id, name, email, cohort` (`student_id` still accepted as an
alias for `enrollment_id`, for older CSVs; `cohort` is optional). There is no password
column: `email` is the magic-link sign-in identity, so it's required and must be unique.
```csv
enrollment_id,name,email,cohort
STU1001,Asha Rao,asha@example.com,2026
STU1002,Vikram Shah,vikram@example.com,2026
```

**MCQ bulk** — `body_md, option_a, option_b, option_c, option_d, correct, marks, negative_marks`
```csv
body_md,option_a,option_b,option_c,option_d,correct,marks,negative_marks
What is 2 + 2?,3,4,5,6,B,2,0.5
```

---

## Load testing

```bash
cd backend
python -m scripts.loadtest --students 400 --duration 300
```

Simulates the real traffic mix — logins, auto-save every 10 s, heartbeats every 30 s,
navigation — and reports p50/p95/p99 latency per endpoint. Run at 1.5× expected
concurrency before an exam.

The two numbers that predict an exam-day incident are **auto-save p99** and **flush
lag** (`dirty_attempts` set size). Watch those, not CPU.

---

## Pre-exam checklist

- [ ] `JWT_SECRET` set to a real random value, not the default
- [ ] `BREVO_API_KEY` and `BREVO_SENDER_EMAIL` set and the sender verified in Brevo,
      `ENVIRONMENT=production` set — without a working key, no student can sign in
- [ ] Load test passed at 1.5× expected concurrency
- [ ] Redis AOF persistence on (`appendfsync everysec`)
- [ ] Auto-submit sweeper verified against a seeded overdue attempt
- [ ] Judge images pre-pulled on the judge host for every language the exam uses
      (first pull is slow)
- [ ] Postgres snapshot taken immediately before the window opens
- [ ] Beat worker running. Without it, answers never flush and nothing auto-submits

---

## Known limitations

- **Proctoring is advisory.** Tab-switch and focus-loss events are logged for human
  review, not enforced. Browser lockdown is trivially bypassable, and hard-failing an
  exam on a spurious blur event is worse than the cheating it prevents.
- **Worst-case answer loss is one flush interval (~120 s by default, `AUTOSAVE_FLUSH_SECONDS`)**,
  and only if Redis dies
  outright. AOF persistence bounds this further.
- **The judge trusts Docker as the isolation boundary.** For untrusted code at higher
  stakes, run judge workers on a dedicated host with gVisor or Firecracker.
- **Analytics run against the primary** in this scaffold. Point them at a read replica
  before running reports during a live exam.
