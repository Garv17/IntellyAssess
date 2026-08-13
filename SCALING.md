# Scaling to 200+ concurrent students

Context for this doc: the judge queue used to be a single Celery queue (`judge`)
shared by interactive "Run" clicks and final hidden-case grading, with no rate
limiting, no dedup, and several config knobs (`JUDGE_CPUS`, `JUDGE_MEMORY_MB`) that
were silently ignored because `docker-compose.yml` never actually passed them
through. Under a 200-student exam — bursts of simultaneous Run clicks, and a
deadline spike that can enqueue up to 500 grading jobs in one auto-submit sweep
tick — that shared queue meant a grading spike could delay every student's
interactive Run. This doc covers what changed, what's still deferred and why, and
how to load-test and size a production deployment.

---

## 1. What changed

**Queue separation.** `judge.run` (interactive) and `judge.grade_attempt`/
`judge.preview_sql` (background) now route to separate Celery queues, `judge_run`
and `judge_grade`, each with its own worker service in `docker-compose.yml` and
independently configurable concurrency (`JUDGE_RUN_CONCURRENCY`,
`JUDGE_GRADE_CONCURRENCY`). A grading spike fills up `judge_grade`'s workers; it
cannot touch `judge_run`'s.

**Config actually wired through.** `JUDGE_CPUS`, `JUDGE_MEMORY_MB`,
`JUDGE_PIDS_LIMIT`, `JUDGE_COMPILE_TIMEOUT_SECONDS`, `RUN_RATE_LIMIT_SECONDS` are
now passed from the root `.env` into every backend/judge container via
`docker-compose.yml`'s `x-backend-env` block. Previously only `sandbox.py`'s
Python-side defaults applied, regardless of what was in `.env`.

**Resource caps on `postgres`/`redis`/`backend`** (`cpus:`/`mem_limit:`, all
env-overridable) so a judge burst can't starve the API or DB — nothing enforced
that before. **Defaults assume the 4-vCPU box this doc is sized for — check
`nproc` and raise these proportionally on a bigger host.** §5's second load test
found these caps causing exactly the starvation they were meant to prevent, on
an 8-core host that kept the defaults: `BACKEND_CPU_LIMIT=1.5` alone was enough
to fail 105 of 120 simultaneous logins before judge was ever involved.

**Per-attempt Run rate limit** (`RUN_RATE_LIMIT_SECONDS`, default 20s), Redis-backed
(`SET NX EX`, same idiom as the existing magic-link cooldown), returns a friendly
429 with `Retry-After`. The student's code is unaffected — autosave is independent
of the rate limit.

**Run dedup**, keyed on `attempt_id + question_id + language + code_hash + mode +
target`. An identical in-flight request (double-click, frontend/network retry)
gets back the *same* `run_id` instead of creating a second job — this runs before
the rate limit, so retries of your own last request are always free.

**Compile timeout.** `sandbox.py`'s per-test-case execution already wrapped every
run in `timeout -s KILL`; the *compile* step didn't have an equivalent wrapper, so
a hung compile (e.g. a template-instantiation bomb) could only be reaped once the
container's own session-length `sleep` bound expired. Fixed via
`JUDGE_COMPILE_TIMEOUT_SECONDS` (default 20s), with a distinct `error_kind`
("compile" vs "infra" vs "validation") now threaded back through `sandbox.execute()`
so failures are attributable, not lumped together.

**Judge metrics.** No Prometheus/statsd existed in this project, so this reuses
Redis (already in the stack): `INCR`-based counters for enqueued/started/done/
error/compile-failed/timeout per queue, plus queue depth (`LLEN` on the broker) and
active-task counts (Celery `inspect()`), exposed at `GET /api/admin/judge/metrics`.

**Deadline sweep index.** `maintenance.auto_submit_expired` filters on
`status='in_progress' AND deadline_at<=now()`; the existing composite index only
covered `(exam_id, status)`. A new partial index (`ix_attempts_deadline_in_progress`,
migration `seb3`) covers `deadline_at` for in-progress attempts.

**Frontend poll jitter.** The Run-result poll (`CodingView.tsx`/`SqlWorkspace.tsx`)
was a fixed 1200ms `setInterval`; it's now a jittered 1000-1400ms `setTimeout`
chain, so many students polling after a simultaneous Run burst don't do it in
lockstep.

**Warm container pool** (`app/services/judge_pool.py`) — implemented, **disabled
by default** (`JUDGE_POOL_ENABLED=false`). See §7 for the full design and the
leak test that must pass before turning it on anywhere real.

---

## 2. What's deferred, and why

**WebSocket push for run results.** The admin Live Monitor already has the
plumbing (`pubsub.py` + `ws_manager.py`: Redis pub/sub → per-worker socket
registry → broadcast, needed because there are multiple uvicorn workers). Wiring
student run-results to the same pattern would remove polling entirely instead of
just jittering it, but it's a real feature addition (new channel, per-student auth
on the socket, a frontend hook) — deferred in favor of the lower-risk polling
jitter for this pass.

**Live CPU-count-based concurrency auto-tuning.** `JUDGE_RUN_CONCURRENCY`/
`JUDGE_GRADE_CONCURRENCY` are env vars with a documented conservative default for
a 4-vCPU box, not something computed from `os.cpu_count()` at container startup.
Shell arithmetic in a Compose `command:` string is brittle; an explicit env var
with a documented formula (§3 below) is easier to reason about and override.

**Actual second-host judge deployment.** §4 below documents the recipe; it hasn't
been tested against a real second host in this environment.

---

## 3. Sizing the judge concurrency

Each sandbox container claims `JUDGE_CPUS` (default 1.0) real cores while it runs.
Total judge core demand ≈ `(JUDGE_RUN_CONCURRENCY + JUDGE_GRADE_CONCURRENCY) ×
JUDGE_CPUS`. Reserve the rest of the box for `postgres`/`redis`/`backend`/`worker`/
`beat` (the `POSTGRES_CPU_LIMIT`/`REDIS_CPU_LIMIT`/`BACKEND_CPU_LIMIT` env vars cap
those explicitly now).

Defaults (`JUDGE_RUN_CONCURRENCY=2`, `JUDGE_GRADE_CONCURRENCY=1`, `JUDGE_CPUS=1.0`)
assume a 4-vCPU box: ≤3 cores of judge demand, ~1 core left for everything else.
On a bigger box, raise `JUDGE_RUN_CONCURRENCY`/`JUDGE_GRADE_CONCURRENCY`
proportionally via `.env` — no code change needed. Don't just raise them past what
the box's core count supports; that's oversubscription, not more capacity (see the
load test below for what oversubscription actually looks like).

## 4. Running judge workers on a dedicated host

Since there's no shared local state — everything judge-related goes through Redis
(broker) and Postgres — `judge_run`/`judge_grade` can run on a separate host from
`postgres`/`redis`/`backend`:

1. On the judge host, run only the `judge_run` and `judge_grade` services (e.g.
   `docker compose up judge_run judge_grade`, or a trimmed compose file with just
   those two services plus the shared network config).
2. Point that host's `.env` `DATABASE_URL`/`DATABASE_URL_SYNC`/`REDIS_URL`/
   `CELERY_BROKER_URL`/`CELERY_RESULT_BACKEND` at the main host's exposed Postgres/
   Redis (not `postgres:5432`/`redis:6379` — those only resolve inside the main
   host's Docker network).
3. Size `JUDGE_RUN_CONCURRENCY`/`JUDGE_GRADE_CONCURRENCY` for the judge host's own
   core count per §3, independent of the main host's `.env`.
4. Pre-pull the judge language images on that host too (SETUP.md step 6) — the
   Docker image cache is per-host, not shared.

## 5. Load testing

`backend/scripts/loadtest.py` now has two modes, plus `ramp_loadtest.py` ramps
either one across increasing student counts:

```powershell
# Test A: normal mixed traffic (autosave/heartbeat/nav/submit)
python -m scripts.loadtest --students 200 --duration 300

# Test B/C: simultaneous Run burst — every student does exactly one Run, no autosave noise
python -m scripts.loadtest --students 100 --mode run_spike --stagger 0

# Test D: grade spike — short duration + no stagger means every student submits
# (with a pending coding answer) within seconds of each other; submit_exam enqueues
# judge.grade_attempt synchronously, so this doesn't need a real deadline or the sweep
python -m scripts.loadtest --students 200 --duration 5 --stagger 0

# Sustained Run load: every student clicks Run at --rpm for the whole --duration,
# not just once — models real usage over an exam window, not a single burst.
# The frontend disables Run until the previous one completes, so this respects
# that constraint and reports achieved rpm alongside whatever you asked for.
python -m scripts.loadtest --students 120 --mode run_repeated --rpm 10 --duration 600 --stagger 0

# Ramp either spike mode to find the breaking point
python -m scripts.ramp_loadtest --stages 25,50,100,200 --mode run_spike
```

Cross-check client-observed `run wall time` against `GET /api/admin/judge/metrics`
(`run_avg_queue_wait_ms` vs `run_avg_exec_ms`) to see how much of the latency is
queueing (concurrency-bound, tune `JUDGE_RUN_CONCURRENCY`) vs. actual judge
execution (container-lifecycle-bound — see §7, the warm pool targets exactly this).

### Observed results (this environment, 4-vCPU dev box, defaults unchanged)

A 30-student simultaneous Run burst (`--mode run_spike --stagger 0`) against the
default `JUDGE_RUN_CONCURRENCY=2`:

| Metric | Value |
|---|---|
| `run_avg_queue_wait_ms` | ~23,000-25,000ms |
| `run_avg_exec_ms` | ~8,000-8,400ms |
| Client-observed run wall time (p50 / p99) | ~35,000ms / ~48,000ms |
| `judge_grade` queue during this | untouched — 0 pending/active |

A follow-up 30-student grade spike (short-duration submits) enqueued 18 grading
jobs into `judge_grade` (`JUDGE_GRADE_CONCURRENCY=1`) and fully drained them (0
pending/active on recheck) **without any measurable effect on `judge_run`** —
this is the queue-separation working as intended. Manually verified in the same
session: two concurrent identical Run requests for one student returned the same
`run_id` (dedup), and a third, *different* request 1s later got a 429 with
`Retry-After: 20` (rate limit) while the first two were unaffected.

### Observed results — sustained load, 120 students × 10 req/min for 10 minutes

A materially harder test than the spike above: 120 students, each clicking Run
every ~6s (10/min) for 10 minutes straight, `JUDGE_RUN_CONCURRENCY=2` and pooling
still disabled — i.e. 120× the concurrent demand the box's 2 judge slots were
sized for, sustained rather than a single burst.

| Metric | Value |
|---|---|
| Requested rate | 10 req/min/student (1,200 req/min total) |
| **Achieved rate** | **~0.9 req/min/student** (~108 req/min total) |
| `judge_run` pending queue, checked right after the 10-minute window ended | **648** |
| `run_avg_queue_wait_ms` (cumulative, climbing throughout) | ~269,000-297,000ms (**4.5-5 minutes**) |
| Client-side poll timeouts (gave up after 60s without a result) | **965 of 1,038 submissions (93%)** |

**This configuration cannot sustain this load — not "slow," actually falling
behind.** Demand (1,200 req/min) vastly exceeds the ephemeral path's throughput
ceiling at this concurrency (2 slots × ~8s/run ≈ 15 completions/min); the queue
grew for the entire 10-minute window and was still 648 deep when new submissions
stopped. It only returned to empty because the test ended and the backlog was
manually purged (`celery -A app.tasks.celery_app.celery_app purge -Q judge_run`)
— left alone, it would have kept draining at roughly the same ~15/min ceiling,
correctly ordered but each waiting minutes behind the last.

**The achieved-rate number above is optimistic, not pessimistic**, because the
test script gives up polling after 60s and moves on to the next submission —
the real frontend never does that; the Run button stays disabled indefinitely
until the job actually completes. A real 120-student class doing this would see
*fewer* completed Runs per student than measured here, not more, since nothing
would ever let them submit again before their stuck request finally resolved.

### Observed results — same test, multi-language exam, exposes a different bottleneck

Re-ran the same shape of test (120 students, 1200 req/min target, 10 minutes)
against a different exam — 1 DSA problem (second largest number, across Python/
JS/C/C++/Java) + 1 SQL problem (second highest salary) — with `loadtest.py`'s
`run_repeated` mode extended to cycle through every allowed language per
question, not just one. Result was dominated by a different, earlier bottleneck:

| Metric | Result |
|---|---|
| Students who got past login at all | **15 of 120** |
| `magic_request` (login) — mean / p99 latency | ~15,800ms / ~33,000ms |
| `magic_request` outright timeouts (>30s) | 92 of 120 |
| Successful Run submissions (from the 15 who got through) | 187, evenly spread across all 6 languages (16-17.6% each) |
| Achieved rate (of those 15) | ~1.2 req/min/student vs. 10 requested |

**Root cause: not judge capacity — the `BACKEND_CPU_LIMIT=1.5` cap added earlier
in §1, on a host that actually has 8 real cores.** That cap was meant to stop a
judge burst from starving the API; on this specific dev machine it did the
opposite — it starved the API on its own, before judge was ever the bottleneck,
because 1.5 CPUs isn't enough to serve 120 simultaneous login requests regardless
of what judge is doing. `docker inspect backend` confirmed `NanoCpus: 1500000000`
against an 8-core host.

**Takeaway: the resource caps in §1 (`POSTGRES_CPU_LIMIT`, `BACKEND_CPU_LIMIT`,
etc.) are sized for the 4-vCPU box this doc assumes throughout — they are not
safe defaults to leave unchanged on a bigger host.** Left as-is, they cap the API
below what the host could actually give it, and a login burst alone (not even
judge load) is enough to hit that ceiling. Before load-testing — or deploying —
on any host, check `nproc`/actual core count against these caps and size them
proportionally, the same way `JUDGE_RUN_CONCURRENCY` already gets called out in
§3.

**A second, independent cause was found on closer investigation** (a login
failure is not acceptable at any scale, so this got root-caused rather than
just having the CPU cap raised): `POST /api/auth/student/magic-link/request`
was making a real outbound HTTPS call to Brevo **even in development**
(`auth.py`, `request_magic_link`), despite the response already handing back a
`dev_token` regardless of whether that email send succeeded. Under 120
simultaneous logins that's 120 real TLS handshakes competing for CPU, each one
also holding a pooled database connection open for its duration — a second
bottleneck compounding the CPU cap, independently capable of causing exactly
the timeout pattern observed (SQLAlchemy's default 30s pool-checkout timeout
lines up with the 92 timeouts seen).

**Fix:** skip the Brevo call entirely when `ENVIRONMENT=development` (it was
already pointless there), and raise `BACKEND_CPU_LIMIT`/`POSTGRES_CPU_LIMIT`/
`REDIS_CPU_LIMIT` to match this host's real 8 cores. Verified in isolation
first — 120 truly simultaneous logins, no judge involved — after the fix:

| Metric | Before | After |
|---|---|---|
| Logins succeeded | 15 of 120 (105 timed out) | **120 of 120** |
| Mean login latency | ~15,800ms | **~2,300ms** |
| Max login latency | >30,000ms (timeout) | **~2,700ms** |

**Then re-ran the full 120-student, 1200-req/min, multi-language test with the
fix in place** — same shape as before, fresh student range so there's no
leftover exam state:

| Metric | Before the fix | After the fix |
|---|---|---|
| Students who reached the judge system at all | 15 of 120 | **119-120 of 120** |
| Successful Run submissions | 187 | **1,172** |
| Language split | even, 16-17.6% each | even, **15.9-16.9% each** |
| `judge_run` pending queue after the window | (never reached this scale) | **769** |

With login no longer the gate, demand genuinely reached judge at the intended
scale — and the result is exactly what §5's first sustained-load test already
predicted: `judge_run`'s 2 concurrent slots cannot sustain 1200 req/min from a
real 120-active-student class, compiled languages (Java/C++) included, and the
queue fell behind the same way. **This is now a clean, trustworthy measurement
of the judge bottleneck specifically** — the login-stage confound from the
first attempt is gone. The fix for *this* (unlike the login issue) is the
already-documented one: raise `JUDGE_RUN_CONCURRENCY`, or enable the warm pool
in §7 after its leak test passes.

**Reading these numbers:** the ~8.4s average *exec* time for a trivial `print(1)`
is almost entirely Docker container create/start/exec/remove overhead, not actual
compute — this is exactly the cost §7's warm pool exists to cut. The ~24s average
*queue* wait at concurrency=2 for a 30-request burst is expected math (30 requests
/ 2 concurrent slots × ~8s each ≈ the observed order of magnitude) and is exactly
what raising `JUDGE_RUN_CONCURRENCY` on a bigger box, or enabling the pool, would
each independently improve. These numbers were captured with the pool disabled
(its default); re-run the same test with `JUDGE_POOL_ENABLED=true` after the leak
test passes to see the actual delta.

## 6. Is 4 vCPUs enough for 200 students?

**For steady-state autosave/heartbeat/navigation traffic: yes** — that path was
already async, Redis-buffered, and batch-flushed before this change, and the
sizing math in `DESIGN.md` §1 holds at 200 students (well under the 400-student
budget it was written for).

**For interactive Run under sustained load: no, not at default concurrency** —
the 120-student/10-req-min sustained test above isn't a marginal degradation,
it's the queue falling permanently behind (648 deep and still growing when the
test ended). If real Run usage is closer to sustained than a single spike (a
class actively iterating on SQL/code for most of the exam, not just at t=0),
size `JUDGE_RUN_CONCURRENCY` and/or enable the warm pool (§7) *before* that
volume shows up in production — this isn't a "tune it if it gets slow" problem,
it's a hard ceiling that gets worse the longer sustained load continues.

**For interactive Run under a simultaneous burst: not comfortably**, at the
default `JUDGE_RUN_CONCURRENCY=2`. The load test above shows ~35-48s wall time for
the tail of a 30-student simultaneous burst; 200 students clicking Run at the same
instant (worst case, unlikely in practice since real classes stagger naturally)
would be materially worse under the same concurrency. Options, cheapest first:
raise `JUDGE_RUN_CONCURRENCY` (bounded by spare cores — see §3), move judge workers
to a dedicated host (§4), or run the leak test and enable the warm pool (§7) to cut
the ~8s-per-run cold-start cost that dominates the exec side of that math.

**Recommended production judge-server sizing:** §7's measured results (leak test
passed, ~5x exec-time improvement, ~3x wall-time improvement with the pool
enabled) make the pool the clearly better lever over just adding cores. Without
it, hitting ≤10s p99 wall time on a 200-student simultaneous burst needs roughly
`JUDGE_RUN_CONCURRENCY ≈ 20-25` at `JUDGE_CPUS=1.0` (~20-25 dedicated cores for
`judge_run` alone, extrapolating from the ~8s/run ephemeral cost). With the pool
enabled, the same target needs only a fraction of that — the measured ~1.6s/run
pooled exec cost means the same 2-slot concurrency that produced ~18.6s p99 at 30
students should scale to a 200-student burst with materially less added
concurrency than the ephemeral path would require. Recommendation: run the leak
test in your actual production Docker environment, enable the pool, and size
`JUDGE_RUN_CONCURRENCY` from a real 200-student `run_spike` load test against that
result — don't scale core count as a substitute for the pool if this exam program
is going to keep growing past 200.

## 7. Warm container pool (`app/services/judge_pool.py`)

Keeps a fixed number of long-lived containers per language (`judge-pool-{language}-{i}`
for `i` in `range(JUDGE_POOL_SIZE_PER_LANGUAGE)`, default 3 — the combined
worst-case of `JUDGE_RUN_CONCURRENCY`+`JUDGE_GRADE_CONCURRENCY`, shared by both
queues) instead of creating/destroying a container per run. `sandbox.execute()`
tries a checkout first when `JUDGE_POOL_ENABLED=true`; a miss (pool exhausted, a
request needing more memory than `JUDGE_POOL_MEMORY_MB` provides, or pooling
disabled) falls straight through to the original ephemeral create/destroy path —
pooling is purely additive, never a hard dependency.

**What makes reuse safe, not just fast:**
- **Workspace wipe on every checkin** — `rm -rf` on both `/sandbox` and `/tmp`
  (both tmpfs, both `mode=1777`, so file permissions set by student code never
  block removal).
- **`init=True` on pooled containers** — Docker injects `tini` as real PID1
  instead of the plain `sleep` the ephemeral path uses, so orphaned/zombie
  processes from a killed run actually get reaped between reuses. This is the
  one piece that didn't exist before and is why reuse was unsafe until now.
- **Health check before every handout** — container status + a process-count
  probe; anything off discards and transparently rebuilds under the same slot
  name, so a caller can never tell "reused" apart from "silently rebuilt."
- **Redis-coordinated checkout** (`SET NX EX`, same idiom as the existing
  run-cooldown/magic-link locks) so Celery's separate prefork worker processes
  can never be handed the same slot.

**Operational note confirmed during testing:** pooled containers
(`judge-pool-{language}-{i}`) are created directly via the Docker API from inside
`judge_run`/`judge_grade`, not declared as `docker-compose.yml` services — so
`docker compose down` does **not** remove them; they're independent sibling
containers that outlive the compose project. If you disable pooling or tear down
a stack for good, clean them up explicitly:
`docker rm -f $(docker ps -aq --filter "name=judge-pool-")`.

**Before enabling anywhere real:** run the leak test —
`docker compose exec judge_run python -m scripts.judge_pool_leak_test` — and
confirm every check passes. It proves, against your actual Docker daemon and
images, that: two checkouts of a size-1 pool reuse the same container; marker
files from one execution don't survive into the next; a deliberately orphaned
process is reaped, not left as a zombie; and a container killed out-of-band is
detected and rebuilt rather than handed out broken. Only then flip
`JUDGE_POOL_ENABLED=true` via `.env`, and re-run the §5 load test to measure the
actual improvement over the documented baseline.

### Observed results (this environment, leak test + before/after load test)

Leak test: all 5 checks passed against the real Docker daemon and the `python`
image. Empirically confirmed `init=True` is doing real work, not decoration: the
same orphan-then-exit scenario run against a control container built with
`init=False` left a zombie behind (`ps` count 5→6, never reaped); the pooled
container (`init=True`) stayed flat (6→6) across the same scenario.

30-student simultaneous Run burst, `JUDGE_RUN_CONCURRENCY=2` unchanged, only
`JUDGE_POOL_ENABLED` flipped — same load test as §5, fresh student range so the
comparison isn't polluted by cache warmth from the earlier run:

| Metric | Pool disabled (§5 baseline) | Pool enabled | Change |
|---|---|---|---|
| `run_avg_exec_ms` | ~8,060 | ~1,600 | **~5x faster** |
| `run_avg_queue_wait_ms` | ~23,000-25,000 | ~9,400 | **~2.5x lower** |
| Client wall time (mean / p99) | ~34,500 / ~48,100 | ~11,700 / ~18,600 | **~3x lower** |

(`run_avg_exec_ms`/`run_avg_queue_wait_ms` are cumulative counters — the pooled
figures above are the delta between two `/api/admin/judge/metrics` reads,
isolating just this test's 29 runs from the session total.)

The queue-wait drop is a downstream effect, not a separate fix: concurrency is
still 2, but each slot now turns over in ~1.6s instead of ~8s, so a 30-request
burst drains roughly 5x faster through the same two slots. This is the numbers
the deferred-item framing predicted — pooling removing the dominant cost instead
of parallelizing more of it — now actually measured rather than estimated.
`judge_grade` was untouched throughout (queue separation holding up under this
change exactly as under the baseline).
