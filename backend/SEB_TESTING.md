# Testing the SEB integration locally

## What's implemented

| Area | Files |
|---|---|
| Config Key verification | `app/seb.py` (`verify_seb_request`) |
| Gating | `app/deps.py` (`attempt_exam`, wraps `active_attempt`; `writable_attempt` now wraps `attempt_exam`); `app/routers/student.py` (`start_exam`, `exam_state`, `exam_questions`, `heartbeat`, `submit_exam`) |
| Exam config | `Exam.requires_seb`, `Exam.seb_config_key`, `ExamAttempt.seb_verified` (`app/models.py`); `ExamSummaryOut.requires_seb` (`app/schemas.py`); migration `alembic/versions/0001_add_seb_fields.py` |
| Multi-student PIN login | `ExamInvite` model; `app/routers/invites.py` (`GET /api/invite/redeem`, `POST /api/invite/pin-login`); `POST /api/admin/exams/{exam_id}/invites`; `send_invite_email(_sync)` + `send_bulk_invites` task; migration `0002_add_exam_invites.py` |
| Frontend | `Dashboard.tsx` (launch link vs. "Start exam", detected via `navigator.userAgent`); `InviteLanding.tsx` (`/invite?token=`); `PinLogin.tsx` (`/pin-login?exam=`); `api.ts` |

**No admin UI yet** for toggling `requires_seb`/`seb_config_key` (direct SQL) or for
triggering invites (`POST /api/admin/exams/{exam_id}/invites` via curl/Swagger).

**All of this is baked into the `backend`/`worker`/`ai-worker`/`beat` Docker images at build
time** — `docker-compose.yml` has no source bind-mount. `docker compose up -d --build`
(not a plain `up -d`) is what picks up code changes.

---

## Before you start: the Host-header gotcha

The Config Key check hashes `requested_url + config_key` and compares it to the
header SEB sent. `requested_url` is whatever URL the server thinks it received:

| How you run it | Host header FastAPI sees | Check works? |
|---|---|---|
| `docker compose up` (nginx → api) | `localhost:5180` (nginx preserves it: `proxy_set_header Host $host;`) | ✅ |
| `npm run dev` (Vite) + `uvicorn --reload` | `localhost:8000` (Vite's proxy default `changeOrigin: true` rewrites it) | ❌ always 403 |
| curl straight at `:8000`, no frontend | `localhost:8000` | ✅, if you hash against the `:8000` URL |

**Use `docker compose up` for the GUI tests below.** For hot-reload frontend dev
instead, set `changeOrigin: false` on both proxy entries in `frontend/vite.config.ts`
(dev-only file, not used by the Docker build).

---

## Tier 1 — command-line smoke test (no SEB client)

Confirms the backend logic before touching the real Config Tool. Uses a made-up
config key — this tests the hash math, not a real `.seb` file.

```bash
docker compose up -d --build postgres redis backend
docker compose exec backend python -m scripts.seed
```

Open Docker Desktop → `intellyassess` → `backend` → **Logs**, leave it open — every call
below shows up as a normal uvicorn access-log line (`... 403 Forbidden` / `200 OK`).

```bash
docker compose exec postgres psql -U exam -d exam -c \
  "SELECT id, title FROM exams WHERE title='Demo Placement Test';"

docker compose exec postgres psql -U exam -d exam -c \
  "UPDATE exams SET requires_seb = true, seb_config_key = 'test-key-123' WHERE title = 'Demo Placement Test';"
```

```bash
curl -s -X POST http://localhost:8000/api/auth/student/magic-link/request \
  -H "Content-Type: application/json" -d '{"email":"stu0001@example.com"}'
# → {"dev_token": "eyJ..."}   (dev-only field, ENVIRONMENT=development)

curl -s -X POST http://localhost:8000/api/auth/student/magic-link/verify \
  -H "Content-Type: application/json" -d '{"token":"PASTE_DEV_TOKEN_HERE"}'
# → {"access_token": "eyJ...", ...}
```

```bash
EXAM_ID="PASTE_EXAM_ID_HERE"
ACCESS="PASTE_ACCESS_TOKEN_HERE"
URL="http://localhost:8000/api/exam/$EXAM_ID/start"

curl -s -o /dev/null -w "no header:    %{http_code}\n" -X POST "$URL" \
  -H "Authorization: Bearer $ACCESS"

HASH=$(python3 -c "import hashlib,sys; print(hashlib.sha256((sys.argv[1]+sys.argv[2]).encode()).hexdigest())" "$URL" "test-key-123")
curl -s -o /dev/null -w "correct hash: %{http_code}\n" -X POST "$URL" \
  -H "Authorization: Bearer $ACCESS" -H "X-SafeExamBrowser-ConfigKeyHash: $HASH"

curl -s -o /dev/null -w "wrong hash:   %{http_code}\n" -X POST "$URL" \
  -H "Authorization: Bearer $ACCESS" \
  -H "X-SafeExamBrowser-ConfigKeyHash: 0000000000000000000000000000000000000000000000000000000000000"
```

```bash
# other gated routes inherit the check via attempt_exam
STATE_URL="http://localhost:8000/api/exam/state"
curl -s -o /dev/null -w "state, no header:    %{http_code}\n" "$STATE_URL" -H "Authorization: Bearer $ACCESS"
HASH2=$(python3 -c "import hashlib,sys; print(hashlib.sha256((sys.argv[1]+sys.argv[2]).encode()).hexdigest())" "$STATE_URL" "test-key-123")
curl -s -o /dev/null -w "state, correct hash: %{http_code}\n" "$STATE_URL" \
  -H "Authorization: Bearer $ACCESS" -H "X-SafeExamBrowser-ConfigKeyHash: $HASH2"

docker compose exec postgres psql -U exam -d exam -c \
  "SELECT seb_verified FROM exam_attempts ORDER BY created_at DESC LIMIT 1;"
```

| Call | Expected |
|---|---|
| no header | `403` |
| correct hash | `200` |
| wrong hash | `403` |
| `/state` no header | `403` |
| `/state` correct hash | `200` |
| `seb_verified` | `t` |

Reset: `UPDATE exams SET requires_seb = false WHERE title = 'Demo Placement Test';`

---

## Tier 2 — single student through the real SEB client

Fastest way to confirm the real Config Tool + real SEB client actually work end to
end. Not the multi-student design — see Tier 3 for that; the Start URL here embeds
one specific student's own login token, which only works for one active student at a
time (§5 of `SEB_INTEGRATION.md` explains why).

```bash
docker compose up -d --build
docker compose exec backend python -m scripts.seed
```

7 containers should come up: `postgres`, `redis`, `backend`, `worker`, `ai-worker`,
`beat`, `frontend`. Keep `backend`'s Logs tab open.

```bash
curl -s -X POST http://localhost:8000/api/auth/student/magic-link/request \
  -H "Content-Type: application/json" -d '{"email":"stu0002@example.com"}'
# → {"dev_token": "eyJ..."}   — 15 min TTL, single-use
```

**Config Tool:**
1. **General → Start URL**: `http://localhost:5180/auth/magic?token=PASTE_DEV_TOKEN_HERE`
2. **Network → Filter**: activate URL filtering, allow rule `^http://localhost:5180/.*$`
   (nothing else needed — nginx/Vite proxy `/api` and `/uploads` under this one origin)
3. **Security → Kiosk Mode**: `None (for debugging only)`
4. **Exam tab**: tick "Use Browser Exam Key and Config Key," copy the Config Key
   **last**
5. Save as `demo.seb`

```bash
docker compose exec postgres psql -U exam -d exam -c \
  "UPDATE exams SET requires_seb = true, seb_config_key = 'PASTE_CONFIG_KEY_HERE' WHERE title = 'Demo Placement Test';"
```

Double-click `demo.seb`. SEB launches, redeems the magic link, lands on `/dashboard`
already logged in — showing **"Start exam"** (not the launch link, since SEB's own
user agent is detected). Click through: start → answer → submit. Every request in the
`backend` Logs tab should be `200`, never `403`.

```bash
docker compose exec postgres psql -U exam -d exam -c \
  "SELECT seb_verified FROM exam_attempts ORDER BY created_at DESC LIMIT 1;"   # expect t
```

**Negative test** (normal browser, no SEB): reuse an access token from Tier 1 or the
curl login above, open DevTools on `http://localhost:5180`:
```js
localStorage.setItem('exam.access', 'PASTE_ACCESS_TOKEN');
localStorage.setItem('exam.refresh', 'PASTE_REFRESH_TOKEN');
localStorage.setItem('exam.role', 'student');
```
Reload → `/dashboard` shows **"Launch in Safe Exam Browser"** instead of "Start exam"
(no `SEB` in this UA). Forcing the API call anyway → `403`.

**Tamper test:** easiest via Tier 1 (already covered, zero setup). With the real
client: edit any setting in `demo.seb` after copying the Config Key, save, relaunch
with a fresh `dev_token` — every gated request now `403`s with "Safe Exam Browser
configuration does not match this exam."

---

## Tier 3 — concurrent multi-student test via the PIN flow

The real design: one shared `.seb` file and Config Key for the whole exam, identity
established by a PIN typed inside SEB rather than anything in the Start URL.

```bash
docker compose up -d --build
docker compose exec backend python -m scripts.seed

docker compose exec postgres psql -U exam -d exam -c \
  "SELECT id, title FROM exams WHERE title='Demo Placement Test';"
```

**Add a few students with real emails you can receive mail at** — Admin console
(`http://localhost:5180/admin`) → "Add student," `Cohort: 2026` on each (the demo exam
is seeded with that cohort) — or via Swagger (`/docs` → `POST /api/admin/students`).

**Send invites** (curl/Swagger — no Admin UI button for this yet):
```bash
ADMIN_TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/admin/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"Admin@123"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

EXAM_ID="PASTE_EXAM_ID_HERE"

curl -s -X POST "http://localhost:8000/api/admin/exams/$EXAM_ID/invites" \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"student_ids": ["id1", "id2", "id3", "id4", "id5"]}'
```
(Student ids from `SELECT id, email FROM students;` or the Admin console.) This
creates one `ExamInvite` row per student and queues `mailer.send_bulk_invites` —
check the `worker` container's Logs tab for `sent`/`failed` counts. Each student gets
a link to `/invite?token=...` (a normal webpage, safe in any browser).

**Build the one shared `.seb` file** — same Config Tool steps as Tier 2, except:
- **Start URL**: `http://localhost:5180/pin-login?exam=PASTE_EXAM_ID_HERE` — generic,
  no personal token.
- Copy the Config Key, then the same `UPDATE exams SET requires_seb = true,
  seb_config_key = '...'` as before.
- This one file now works for every invited student — distribute it to all of them.

**Run it, per student, in any order:**
1. Open the emailed link → `/invite?token=...` shows the exam title and a 6-digit
   PIN (reloading rotates it — grab the one from whichever load you're about to use).
2. Click **Launch Safe Exam Browser** (or double-click the shared `demo.seb`). SEB
   opens fresh — no session carries over from the normal-browser tab — and loads
   `/pin-login?exam=...`.
3. Type the PIN → real login (reuses `_issue_student_session`, same as magic-link) →
   `/dashboard` → "Start exam" → identical to Tier 2 from here.

Watch `backend`'s Logs tab for distinct `POST /api/invite/pin-login` calls per student,
each `200`, each followed by that student's own `/start`, `/state`, `/heartbeat` —
all under the same unchanged `seb_config_key`.

| Check | Expected |
|---|---|
| Two students' PINs | Different |
| Same PIN reused after login | `401` — "Incorrect or already-used PIN" |
| PIN used after 10 min | `401` — "This PIN has expired" |
| `exam_invites.pin_consumed_at` after login | Set |
| All students' exam requests | `200`, same `seb_config_key` throughout |

```bash
docker compose exec postgres psql -U exam -d exam -c \
  "SELECT student_id, pin_expires_at, pin_consumed_at FROM exam_invites WHERE exam_id = 'PASTE_EXAM_ID_HERE';"
```

---

## Cleanup

```bash
docker compose exec postgres psql -U exam -d exam -c \
  "UPDATE exams SET requires_seb = false WHERE title = 'Demo Placement Test';"
docker compose down
```
