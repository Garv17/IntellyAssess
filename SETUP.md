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

## 2. Set a signing key

```powershell
cd C:\Users\BAPS\Downloads\Exam_Plateform
$env:JWT_SECRET = python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 3. Start the stack

```powershell
docker compose up --build -d
```

First build: 3–6 minutes. Starts `postgres`, `redis`, `api`, `worker`, `beat`, `judge`,
`frontend`.

## 4. Wait for it to be healthy

```powershell
docker compose ps                    # wait for postgres + redis = (healthy)
curl http://localhost:8000/ready
```

Expect `{"status":"ok","checks":{"database":"up","redis":"up"}}`.

## 5. Create tables + demo data

```powershell
docker compose exec api python -m scripts.seed --students 25
```

Prints the logins. Safe to re-run.

| Role | Login |
|---|---|
| Student | `STU0001` / `Student@123` |
| Admin | `admin@example.com` / `Admin@123` |

## 6. Pre-pull the Python judge image

Skip this and the first "Run code" click hangs ~60s while Docker downloads it.

```powershell
docker pull python:3.11-alpine
```

Add `node:20-alpine`, `gcc:13`, or `eclipse-temurin:21-jdk-alpine` only if you'll use
those languages.

## 7. Sit the demo exam

Open http://localhost:5173, log in as `STU0001` / `Student@123`.

1. Click **Start exam** — the 60-minute timer starts server-side now.
2. Answer an MCQ → indicator shows `Saving…` then `Saved`.
3. **Press F5** — your answer and the correct remaining time come back.
4. Coding section → **Run against samples** → per-case pass/fail.
5. **Submit** → you get a receipt code.

## 8. Verify answers reached Postgres

This is the check that matters. Within ~5s of answering:

```powershell
docker compose exec postgres psql -U exam -d exam -c "SELECT count(*) FROM answers;"
```

**A count of 0 means the `beat` container isn't running.** The exam looks fine to the
student while nothing is saved durably. Check with `docker compose logs beat` — you should
see `flush-answer-buffer` every 5s.

## 9. Check the admin side

Open http://localhost:5173/admin/login in a **private window** (so the student session
stays alive), log in as `admin@example.com` / `Admin@123`.

- **Monitor** — per-student rows, refreshing every 5s. Answered counts lag up to 5s;
  that's expected.
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
docker compose logs -f api      # follow API logs
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
| "Judge is temporarily unavailable" | `docker compose ps judge` — judge worker down |
| First code run hangs ~60s | Image downloading — step 6 |
| `JWT_SECRET is still the default` | Step 2 didn't take effect |
| Logged out / 401 mid-exam | Someone logged in as the same student ID elsewhere. One active session per student is by design — don't test with a student who's already signed in |
| `429 Too Many Requests` on login | Login rate limit (5/min/IP). Wait a minute |
| Port already in use | Change the port mapping in `docker-compose.yml` |

Start over completely:

```powershell
docker compose down -v
docker compose up --build -d
docker compose exec api python -m scripts.seed --students 25
```

---

## Before a real exam

- [ ] Set `JWT_SECRET` (32+ bytes) **and** `ENVIRONMENT=production` — the second one turns
      on the weak-secret check and disables `/docs`
- [ ] Change the Postgres password from `exam:exam` in `docker-compose.yml`
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
celery -A app.tasks.celery_app.celery_app worker -Q judge --pool=threads -c 4 --loglevel=info
```

**Frontend** — fifth terminal:

```powershell
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `localhost:8000`, so no CORS setup is needed.