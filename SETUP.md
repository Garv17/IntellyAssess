# Setup — step by step

Run these in order. Each step assumes the previous one succeeded.

Everything runs in Docker. For running the API/frontend directly on Windows instead, see
[Local development](#local-development) at the bottom.

---

## 1. Start Docker Desktop

Wait until the whale icon stops animating, then confirm:

```powershell
docker version
```

If this errors, the daemon isn't up yet. Don't continue until it works.

## 2. Set the environment

```powershell
Copy-Item .env.example .env
```

Open `.env` and replace `JWT_SECRET` with a real value:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`docker compose` reads this root `.env` itself (separate from `backend/.env`) to fill in
`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, and `JWT_SECRET` in `docker-compose.yml`.

## 3. Start the stack

```powershell
docker compose up --build -d
```

First build: 3–6 minutes. Starts `postgres`, `redis`, `api`, `worker`, `beat`,
`judge_run`, `judge_grade`, `frontend`.

## 4. Wait for it to be healthy

```powershell
docker compose ps                    # wait for postgres + redis = (healthy)
curl http://localhost:8000/ready
```

Expect `{"status":"ok","checks":{"database":"up","redis":"up"}}`.

## 5. Create tables + demo data

```powershell
docker compose exec backend python -m scripts.seed --students 25
```

Prints the admin login and the demo student IDs. Safe to re-run.

| Role | Login |
|---|---|
| Admin | `admin@example.com` / `Admin@123` |
| Student | no password — magic link only, see step 7 (demo student: `STU0001`, `stu0001@example.com`) |

## 6. Pre-pull the judge images

Skip this and the first "Run code" click in a given language hangs ~60s while Docker
downloads its image.

```powershell
docker pull python:3.11-alpine
```

Add `node:20-alpine`, `gcc:13`, `eclipse-temurin:21-jdk-alpine`, and `nouchka/sqlite3:latest`
too if the exam allows JavaScript, C/C++, Java, or SQL.

## 7. Sit the demo exam

Students sign in via a one-time link emailed to them, not a password. In development
there's no need for a real mailbox: the API hands the token straight back.

1. Open http://localhost:8000/docs, find `POST /api/auth/student/magic-link/request`,
   **Try it out** with `{"email": "stu0001@example.com"}`, and execute.
2. Copy the `dev_token` value from the response (only present when
   `ENVIRONMENT=development`, which is the default here).
3. Open `http://localhost:5180/auth/magic?token=<dev_token>` — this signs the student
   in and redirects to the dashboard.
4. Click **Start exam** — the 60-minute timer starts server-side now.
5. Answer an MCQ → indicator shows `Saving…` then `Saved`.
6. **Press F5** — your answer and the correct remaining time come back.
7. Coding section → **Run against samples** → per-case pass/fail.
8. **Submit** → you get a confirmation screen.

In a real (non-development) deployment there is no `dev_token`: the link is only ever
delivered by email, via Brevo.

## 8. Verify answers reached Postgres

This is the check that matters. Within `AUTOSAVE_FLUSH_SECONDS` of answering (15s by
default; the `.env.example` value):

```powershell
docker compose exec postgres psql -U exam -d exam -c "SELECT count(*) FROM answers;"
```

**A count of 0 means the `beat` container isn't running.** The exam looks fine to the
student while nothing is saved durably. Check with `docker compose logs beat` — you should
see `flush-answer-buffer` every 15s (or whatever `AUTOSAVE_FLUSH_SECONDS` is set to). This
is a single batched job draining up to 500 dirty attempts per run, not one write per
student, so it stays cheap at 400 concurrent users even at this interval.

## 9. Check the admin side

Open http://localhost:5180/admin/login in a **private window** (so the student session
stays alive), log in as `admin@example.com` / `Admin@123`.

- **Monitor** — per-student rows, pushed live over WebSocket. Answered counts can still
  lag up to `AUTOSAVE_FLUSH_SECONDS` (15s by default) behind what the student actually
  typed, since that count only reflects what's been flushed to Postgres; that's expected.
- **Export** — downloads results as XLSX.

## 10. Build your own exam

Create exam → add section → add questions → **Publish**.

Made a mistake in a question, an option, or the marked correct answer? Scroll to
**Existing questions** under the section — every question and DI set is listed there with
**Edit** and **Delete** buttons. This still works after publishing, right up until the first
student attempt is created — after that, edits and deletes are blocked so the paper can't
shift under someone mid-exam.

Publish validates the whole paper and refuses with a specific list if anything's wrong
(MCQ without exactly one correct option, coding problem with no hidden test case, DI group
with a missing image). Fix and retry.

---

## Daily commands

```powershell
docker compose logs -f backend      # follow API logs
docker compose down             # stop, keep data
docker compose down -v          # stop, DELETE database
docker compose up --build -d    # start again
```

## If something breaks

| Symptom | Fix |
|---|---|
| Containers stuck in `Created`, never `Up` | `docker compose up` was interrupted while waiting on healthchecks. Just run `docker compose up -d` again |
| `/ready` refuses the connection right after a rebuild | The API needs ~10s to start. Wait for `(healthy)` in `docker compose ps` |
| `/ready` → 503 | Postgres not ready. Wait for `(healthy)` in `docker compose ps` |
| Student can't see an exam you published | Their `cohort` must match the exam's cohort exactly, or the exam's cohort must be blank |
| `409 No exam in progress` | That student already submitted. One attempt per student is enforced in the database |
| `/ready` → `degraded` | Redis down: `docker compose up -d redis`. Exam still works, slower |
| `answers` table stays empty | `beat` container not running — see step 8 |
| Exams don't auto-submit at 0:00 | Same cause: `beat` not running |
| "Judge is temporarily unavailable" | `docker compose ps judge_run judge_grade` — judge worker down |
| First code run hangs ~60s | Image downloading — step 6 |
| `JWT_SECRET is still the default` | Step 2 didn't take effect |
| Logged out / 401 mid-exam | Someone logged in as the same student ID elsewhere. One active session per student is by design — don't test with a student who's already signed in |
| `429 Too Many Requests` on login | Login rate limit (5/min/IP). Wait a minute |
| Port already in use | Change the port mapping in `docker-compose.yml` |
| Magic-link response has no `dev_token` | `ENVIRONMENT` isn't `development`, or you called the endpoint from a non-dev deployment. Real deployments only ever deliver the link by email |
| "This link has already been used" | Magic links are single-use and denylisted on redemption. Request a new one |
| `BREVO_API_KEY is not set` on startup | Required once `ENVIRONMENT` is not `development`, since students can only sign in by email. Set it or stay in development locally |

Start over completely:

```powershell
docker compose down -v
docker compose up --build -d
docker compose exec backend python -m scripts.seed --students 25
```

---

## Before a real exam

- [ ] Set `JWT_SECRET` (32+ bytes) and `ENVIRONMENT=production` (turns on the weak-secret
      and mailer-key checks) **and** `DEBUG=false` separately. `/docs` is gated on
      `DEBUG`, not `ENVIRONMENT` — setting only the latter leaves the API docs public
- [ ] Set `BREVO_API_KEY` and a verified `BREVO_SENDER_EMAIL`. The API refuses to start
      in production without a mailer key, since it's the only way a student signs in
- [ ] Change the Postgres password from `exam:exam` in `docker-compose.yml`
- [ ] Remove the host `ports:` mappings for `postgres` (5432), `redis` (6379), and `api`
      (8000) in `docker-compose.yml`. As shipped they bypass nginx entirely, which means
      no TLS and no rate limiting on the API, and Postgres/Redis reachable from outside
      the host. Only `frontend` (5180, which proxies `/api/`) needs to stay published
- [ ] Put TLS in front — the compose file serves plain HTTP
- [ ] Load test at 1.5× expected concurrency:
      `python -m scripts.loadtest --students 400 --duration 300`
      (watch auto-save p99; target <100 ms)
- [ ] Confirm `beat` is running — the most common exam-day failure
- [ ] Snapshot Postgres right before the window opens

Capacity model and failure analysis: [DESIGN.md](DESIGN.md). CSV formats and repo layout:
[README.md](README.md).

---

## Local development

Run Postgres and Redis in Docker, everything else on Windows directly.

```powershell
docker compose up -d postgres redis
```

**Backend** — run every command from `backend\`, since `.env` is read relative to the
working directory:

```powershell
cd backend
python -m venv .venv                     # a .venv already exists — skip if reusing it
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env              # defaults already point at localhost
python -m scripts.seed --students 25
uvicorn app.main:app --reload
```

**Workers** — three more terminals, each with the venv activated and `cd backend`.
Not optional: without `beat`, answers never persist and exams never auto-submit.

> **`--pool=threads` is required on Windows.** Celery's default prefork pool starts
> cleanly and then processes nothing. Drop the flag on Linux/macOS.

```powershell
celery -A app.tasks.celery_app.celery_app worker -Q default --pool=threads -c 4 --loglevel=info
celery -A app.tasks.celery_app.celery_app beat --loglevel=info
celery -A app.tasks.celery_app.celery_app worker -Q judge_run,judge_grade --pool=threads -c 4 --loglevel=info
```

Local dev runs both judge queues in one worker for simplicity — Docker Compose is
where the run/grade priority split (see [SCALING.md](SCALING.md)) actually
matters, since that's the setup meant to hold up under real exam load.

**Frontend** — fifth terminal:

```powershell
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `localhost:8000`, so no CORS setup is needed.