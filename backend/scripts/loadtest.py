"""Load test that mirrors the real traffic mix rather than hammering one endpoint.

Each simulated student logs in, starts the exam, then loops: auto-save every ~10 s
(mixing MCQ option picks and coding code_text edits, matching the two answer
shapes the real client sends), heartbeat every ~30 s, navigation every ~45 s. That
ratio is what actually decides whether the platform holds, since auto-save
dominates the request budget.

Data-loss check: each simulated student remembers, client-side, the last value it
successfully saved for every question. Right before submitting, it re-fetches
/api/exam/state and diffs the server's merged (Redis-buffer + Postgres) answers
against that ledger — any question the server doesn't have, or has a stale value
for, is a lost write and gets counted/reported.

    python -m scripts.loadtest --students 400 --duration 300

Against the dedicated LT-prefixed cohort seeded by `scripts.seed --loadtest`
(see that script), pinned to ~10 requests/minute/student for a 90-minute run:

    python -m scripts.loadtest --students 250 --duration 5400 --rpm 10 --prefix LT --exam-title "Load Test Exam"

--mode run_spike isolates a burst of interactive "Run" clicks instead (no autosave
noise) — every student does exactly one POST /code/run and polls it to completion:

    python -m scripts.loadtest --students 100 --mode run_spike --stagger 0

A grade-spike (many submits, each with a pending coding answer, close together) is
just --mode mixed with a short --duration and --stagger 0 — no separate mode needed
since submit_exam enqueues judge.grade_attempt synchronously either way:

    python -m scripts.loadtest --students 200 --duration 5 --stagger 0

--mode run_repeated is sustained Run load: every student clicks Run at --rpm for
the whole --duration, not just once. This respects the real UI constraint (the
Run button is disabled until the previous run finishes), so the *achieved* rate
is capped by judge latency — the report shows requested vs. achieved rpm:

    python -m scripts.loadtest --students 120 --mode run_repeated --rpm 10 --duration 600 --stagger 0
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time
from collections import defaultdict

import httpx

BASE_URL = "http://localhost:8000"

latencies: dict[str, list[float]] = defaultdict(list)
errors: dict[str, int] = defaultdict(int)
# Data-integrity ledger, filled in by _verify_no_data_loss. "checked" is every
# question a student successfully saved and then re-verified against the server;
# "lost" is the subset where the server's answer didn't match what was sent.
integrity = {"checked": 0, "lost": 0}
lost_examples: list[str] = []
# End-to-end wall time for one Run click (submit -> polled until done/error), filled
# in by simulate_run_once/simulate_run_repeated. Distinct from latencies["run_submit"],
# which is only the POST's own latency (near-instant — it just enqueues) — run_wall_ms
# is what the student actually experiences: queue wait + judge execution + poll
# granularity.
run_wall_ms: list[float] = []
# Successful run_submit count per language, filled in by simulate_run_repeated —
# shows the actual language mix a multi-language exam produced under load.
run_language_counts: dict[str, int] = defaultdict(int)
# Achieved requests/minute per student, filled in by simulate_run_repeated. The
# frontend disables the Run button until the previous run completes (CodingView.tsx),
# so a requested rate is only achievable if judge latency stays under 60/rpm seconds —
# this is what actually happened, not what was asked for.
run_repeated_rpm_achieved: list[float] = []

# Trivial, always-valid-against-any-schema code per language — the point of
# --mode run_repeated is judge throughput/latency, not grading correctness, so this
# deliberately doesn't need to know a target exam's actual schema.
_TRIVIAL_RUN_CODE = {
    "python": "print({n})",
    "javascript": "console.log({n})",
    "c": '#include <stdio.h>\nint main(){{printf("%d", {n});return 0;}}',
    "cpp": '#include <iostream>\nint main(){{std::cout << {n};return 0;}}',
    "java": 'public class Main {{ public static void main(String[] a) {{ System.out.print({n}); }} }}',
    "sql": "-- iteration {n}\nSELECT {n};",
}


async def timed(label: str, coro):
    started = time.perf_counter()
    try:
        response = await coro
        latencies[label].append((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            errors[f"{label}:{response.status_code}"] += 1
        return response
    except Exception as exc:
        errors[f"{label}:{type(exc).__name__}"] += 1
        return None


async def _login_start_and_fetch(
    client: httpx.AsyncClient,
    student_id: str,
    exam_title: str | None,
    stagger_seconds: float,
) -> tuple[dict[str, str], str, float, list[dict]] | None:
    """Shared setup for every simulation mode: magic-link login, start the attempt,
    fetch the paper. Returns (headers, refresh_token, token_expiry, all_questions),
    or None if any step failed (the caller should just return — a login failure here
    is noise for whatever this simulation is actually trying to measure)."""
    # Stagger logins so an N-student instant login burst doesn't measure the wrong
    # thing (set --stagger 0 to hit auth + exam endpoints immediately instead).
    if stagger_seconds > 0:
        await asyncio.sleep(random.uniform(0, stagger_seconds))

    # Requires ENVIRONMENT=development on the server: that's what makes the request
    # endpoint hand back dev_token instead of only emailing it via Brevo.
    requested = await timed(
        "magic_request",
        client.post(
            "/api/auth/student/magic-link/request",
            json={"email": f"{student_id.lower()}@example.com"},
        ),
    )
    dev_token = requested.json().get("dev_token") if requested else None
    if not dev_token:
        return None

    response = await timed(
        "magic_verify",
        client.post("/api/auth/student/magic-link/verify", json={"token": dev_token}),
    )
    if response is None or response.status_code != 200:
        return None
    tokens = response.json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    refresh_token = tokens["refresh_token"]
    # Refresh a bit before expiry (default access_token_minutes=15) — a run longer
    # than that would otherwise 401 for its entire back half. Jittered so a tight
    # login stagger doesn't put every student's refresh in the same instant 14
    # minutes later — that synchronized burst is what caused a mass ReadTimeout
    # (and silently killed every session at once, no submits, no data-loss check)
    # the first time this ran without jitter.
    token_expiry = time.monotonic() + tokens["expires_in"] - 60 - random.uniform(0, 45)

    exams = await timed("available", client.get("/api/exam/available", headers=headers))
    if exams is None or exams.status_code != 200 or not exams.json():
        return None
    exam_list = exams.json()
    # /available can return other exams the cohort is (or isn't deliberately)
    # scoped out of — never just take [0], or the load test silently exercises
    # the wrong exam (and pollutes its attempts) instead of the LT fixture.
    if exam_title:
        matches = [e for e in exam_list if e["title"] == exam_title]
        if not matches:
            return None
        exam_id = matches[0]["id"]
    else:
        exam_id = exam_list[0]["id"]

    state = await timed(
        "start", client.post(f"/api/exam/{exam_id}/start", headers=headers)
    )
    if state is None or state.status_code != 200:
        return None

    paper = await timed("questions", client.get("/api/exam/questions", headers=headers))
    if paper is None or paper.status_code != 200:
        return None

    all_questions = [q for section in paper.json()["sections"] for q in section["questions"]]
    return headers, refresh_token, token_expiry, all_questions


async def simulate_student(
    client: httpx.AsyncClient,
    student_id: str,
    duration: int,
    autosave_interval: tuple[float, float] = (8.0, 12.0),
    exam_title: str | None = None,
    stagger_seconds: float = 60.0,
) -> None:
    setup = await _login_start_and_fetch(client, student_id, exam_title, stagger_seconds)
    if setup is None:
        return
    headers, refresh_token, token_expiry, all_questions = setup

    mcq_questions = [(q["id"], [o["id"] for o in q["options"]]) for q in all_questions if q["options"]]
    coding_questions = [
        (q["id"], (q["coding_problem"]["allowed_languages"] or ["python"])[0])
        for q in all_questions
        if q["type"] == "coding" and q.get("coding_problem")
    ]
    if not mcq_questions and not coding_questions:
        return

    # Ground truth for the data-loss check: the last value this student believes
    # it successfully saved for each question, keyed the same way AnswerState is.
    saved: dict[str, dict] = {}
    code_buffers: dict[str, str] = {qid: "" for qid, _ in coding_questions}

    deadline = time.monotonic() + duration
    last_heartbeat = 0.0
    last_nav = 0.0

    while time.monotonic() < deadline:
        if time.monotonic() > token_expiry:
            # A single transient failure (timeout, blip) here used to kill the whole
            # session outright — no submit, no data-loss check, and the access token
            # was still valid for another ~60s anyway. Retry a few times with backoff
            # before actually giving up.
            tokens = None
            for attempt in range(3):
                refreshed = await timed(
                    "refresh",
                    client.post("/api/auth/refresh", json={"refresh_token": refresh_token}),
                )
                if refreshed is not None and refreshed.status_code == 200:
                    tokens = refreshed.json()
                    break
                await asyncio.sleep(2 * (attempt + 1))
            if tokens is None:
                return
            headers["Authorization"] = f"Bearer {tokens['access_token']}"
            refresh_token = tokens["refresh_token"]
            token_expiry = time.monotonic() + tokens["expires_in"] - 60 - random.uniform(0, 45)

        # Weighted by how many questions of each type exist, so a 5-MCQ/2-coding
        # paper autosaves MCQs more often than code edits, like a real student would.
        if coding_questions and random.random() < len(coding_questions) / (
            len(mcq_questions) + len(coding_questions)
        ):
            question_id, language = random.choice(coding_questions)
            # Simulate incremental typing: each edit appends a distinct, growing
            # snippet rather than resending the same text, so a stale overwrite
            # under concurrent load is detectable.
            code_buffers[question_id] += f"# edit {len(code_buffers[question_id].splitlines()) + 1}\n"
            answer = {
                "question_id": question_id,
                "code_text": code_buffers[question_id],
                "language": language,
            }
        else:
            question_id, option_ids = random.choice(mcq_questions)
            answer = {"question_id": question_id, "selected_option_id": random.choice(option_ids)}

        response = await timed(
            "autosave",
            client.patch("/api/exam/answers", headers=headers, json={"answers": [answer]}),
        )
        if response is not None and response.status_code == 200:
            saved[question_id] = answer

        now = time.monotonic()
        if now - last_heartbeat > 30:
            last_heartbeat = now
            await timed("heartbeat", client.post("/api/exam/heartbeat", headers=headers))
        if now - last_nav > 45:
            last_nav = now
            await timed("state", client.get("/api/exam/state", headers=headers))

        await asyncio.sleep(random.uniform(*autosave_interval))

    await _verify_no_data_loss(client, headers, student_id, saved)

    receipt = await timed("submit", client.post("/api/exam/submit", headers=headers))
    if receipt is not None and receipt.status_code == 200 and saved:
        answered = receipt.json().get("answered_count")
        if answered != len(saved):
            integrity["lost"] += 1
            if len(lost_examples) < 20:
                lost_examples.append(
                    f"{student_id}: submit receipt answered_count={answered}, expected {len(saved)}"
                )


async def simulate_run_once(
    client: httpx.AsyncClient,
    student_id: str,
    exam_title: str | None = None,
    stagger_seconds: float = 0.0,
    poll_interval: float = 1.0,
    poll_timeout: float = 60.0,
) -> None:
    """Run-spike simulation (spec Test B/C): login, start, then exactly one
    POST /code/run followed by polling — no autosave loop. Isolates what a student
    actually experiences clicking Run under a burst, separate from autosave traffic.
    Appends the end-to-end wall time to run_wall_ms; a run with no coding question,
    a failed submit, or a poll that never reaches done/error before poll_timeout is
    counted as a `run_wall:*` error instead."""
    setup = await _login_start_and_fetch(client, student_id, exam_title, stagger_seconds)
    if setup is None:
        return
    headers, _refresh_token, _token_expiry, all_questions = setup

    coding_questions = [
        (q["id"], (q["coding_problem"]["allowed_languages"] or ["python"])[0])
        for q in all_questions
        if q["type"] == "coding" and q.get("coding_problem")
    ]
    if not coding_questions:
        errors["run_wall:no_coding_question"] += 1
        return
    question_id, language = random.choice(coding_questions)

    started = time.perf_counter()
    response = await timed(
        "run_submit",
        client.post(
            "/api/exam/code/run",
            headers=headers,
            json={"question_id": question_id, "language": language, "code_text": "print(1)"},
        ),
    )
    if response is None or response.status_code != 202:
        errors[f"run_wall:submit_failed_{response.status_code if response else 'none'}"] += 1
        return
    run_id = response.json()["run_id"]

    deadline = time.monotonic() + poll_timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
        result = await timed("run_poll", client.get(f"/api/exam/code/run/{run_id}", headers=headers))
        if result is None:
            continue
        status_value = result.json().get("status") if result.status_code == 200 else None
        if status_value in ("done", "error"):
            run_wall_ms.append((time.perf_counter() - started) * 1000)
            return

    errors["run_wall:poll_timeout"] += 1


async def simulate_run_repeated(
    client: httpx.AsyncClient,
    student_id: str,
    duration: int,
    rpm: float,
    exam_title: str | None = None,
    stagger_seconds: float = 60.0,
    poll_interval: float = 1.0,
    poll_timeout: float = 60.0,
) -> None:
    """Sustained Run load: each student repeatedly clicks Run for `duration`
    seconds at a target `rpm`, cycling through the exam's coding questions. Models
    the real UI constraint (CodingView.tsx disables Run until the previous one
    completes) — the next submission never fires before the current one finishes,
    so the *achieved* rate is capped by judge latency, not just sleep math. That
    gap (requested vs achieved rpm) is itself the interesting number under load."""
    setup = await _login_start_and_fetch(client, student_id, exam_title, stagger_seconds)
    if setup is None:
        return
    headers, _refresh_token, _token_expiry, all_questions = setup

    # Every (question, language) pair, not just each question's first allowed
    # language — a DSA question allowing python/js/c/cpp/java should actually see
    # requests in all five, not just whichever one happens to be listed first.
    coding_questions = [
        (q["id"], language)
        for q in all_questions
        if q["type"] == "coding" and q.get("coding_problem")
        for language in (q["coding_problem"]["allowed_languages"] or ["python"])
    ]
    if not coding_questions:
        errors["run_repeated:no_coding_question"] += 1
        return
    random.shuffle(coding_questions)  # per-student order, so a burst isn't all-one-language

    target_interval = 60.0 / rpm if rpm > 0 else 6.0
    deadline = time.monotonic() + duration
    submitted = 0

    while time.monotonic() < deadline:
        iter_started = time.monotonic()
        question_id, language = coding_questions[submitted % len(coding_questions)]
        template = _TRIVIAL_RUN_CODE.get(language, _TRIVIAL_RUN_CODE["python"])
        code_text = template.format(n=submitted)

        run_started = time.perf_counter()
        response = await timed(
            "run_submit",
            client.post(
                "/api/exam/code/run",
                headers=headers,
                json={"question_id": question_id, "language": language, "code_text": code_text},
            ),
        )
        if response is not None and response.status_code == 429:
            errors["run_submit:429_rate_limited"] += 1
        elif response is not None and response.status_code == 202:
            submitted += 1
            run_language_counts[language] += 1
            run_id = response.json()["run_id"]
            poll_deadline = time.monotonic() + poll_timeout
            while time.monotonic() < poll_deadline:
                await asyncio.sleep(poll_interval)
                result = await timed(
                    "run_poll", client.get(f"/api/exam/code/run/{run_id}", headers=headers)
                )
                if result is None:
                    continue
                status_value = result.json().get("status") if result.status_code == 200 else None
                if status_value in ("done", "error"):
                    run_wall_ms.append((time.perf_counter() - run_started) * 1000)
                    break
            else:
                errors["run_repeated:poll_timeout"] += 1

        elapsed = time.monotonic() - iter_started
        await asyncio.sleep(max(0.0, target_interval - elapsed))

    achieved_rpm = submitted / (duration / 60.0) if duration > 0 else 0.0
    run_repeated_rpm_achieved.append(achieved_rpm)


async def _verify_no_data_loss(
    client: httpx.AsyncClient, headers: dict[str, str], student_id: str, saved: dict[str, dict]
) -> None:
    """Confirms every answer this student believes it saved is actually present,
    with the right value, in the server's merged (Redis-buffer + Postgres) view —
    the same read path /api/exam/state uses for rebuilding the UI after a refresh."""
    if not saved:
        return
    state = await timed("state_verify", client.get("/api/exam/state", headers=headers))
    if state is None or state.status_code != 200:
        integrity["lost"] += len(saved)
        lost_examples.append(f"{student_id}: could not fetch /state to verify ({state})")
        return

    persisted = {a["question_id"]: a for a in state.json()["answers"]}
    for question_id, expected in saved.items():
        integrity["checked"] += 1
        actual = persisted.get(question_id)
        if actual is None or any(actual.get(k) != v for k, v in expected.items()):
            integrity["lost"] += 1
            if len(lost_examples) < 20:
                lost_examples.append(
                    f"{student_id}/{question_id}: expected {expected}, server has {actual}"
                )


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * p / 100))
    return ordered[index]


# Fixed cadence of the other two loop calls (see simulate_student): heartbeat every
# ~30s, nav/state every ~45s. --rpm sizes the autosave interval around what's left
# of the per-minute budget once those are accounted for.
FIXED_CALLS_PER_MINUTE = 60 / 30 + 60 / 45


def autosave_interval_for_rpm(rpm: float) -> tuple[float, float]:
    autosave_per_minute = max(rpm - FIXED_CALLS_PER_MINUTE, 1.0)
    avg_interval = 60 / autosave_per_minute
    return (avg_interval * 0.8, avg_interval * 1.2)


async def main(
    students: int,
    duration: int,
    base_url: str,
    prefix: str,
    rpm: float | None,
    exam_title: str | None,
    stagger: float,
    mode: str = "mixed",
    offset: int = 0,
) -> None:
    autosave_interval = autosave_interval_for_rpm(rpm) if rpm else (8.0, 12.0)
    limits = httpx.Limits(max_connections=students + 50, max_keepalive_connections=students)
    started = time.perf_counter()
    student_range = range(offset + 1, offset + students + 1)

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0, limits=limits) as client:
        if mode == "run_spike":
            # Test B/C (spec §20): all N students click Run at once, no autosave
            # noise. --stagger 0 makes it a true simultaneous burst; the default
            # 60s stagger instead measures a realistic "everyone logs in over a
            # minute, then runs" ramp.
            await asyncio.gather(
                *(
                    simulate_run_once(client, f"{prefix}{i:04d}", exam_title, stagger)
                    for i in student_range
                )
            )
        elif mode == "run_repeated":
            # Sustained Run load: each student clicks Run at --rpm for the full
            # --duration, not just once. No autosave/heartbeat/nav noise — isolates
            # the Run endpoint under a sustained rate rather than a single spike.
            await asyncio.gather(
                *(
                    simulate_run_repeated(
                        client, f"{prefix}{i:04d}", duration, rpm or 10.0, exam_title, stagger
                    )
                    for i in student_range
                )
            )
        else:
            # mode="mixed" is the default autosave/heartbeat/nav/submit traffic. It
            # also doubles as Test D's grade-spike (spec §20): pass a short --duration
            # with --stagger 0 and every student submits (each with a pending coding
            # answer) within seconds of each other — student.py's submit_exam enqueues
            # judge.grade_attempt synchronously on submit, so this fans out N grade
            # jobs into judge_grade without needing a real exam-length deadline or
            # waiting on the auto-submit sweeper.
            await asyncio.gather(
                *(
                    simulate_student(
                        client, f"{prefix}{i:04d}", duration, autosave_interval, exam_title, stagger
                    )
                    for i in student_range
                )
            )

    elapsed = time.perf_counter() - started
    total = sum(len(v) for v in latencies.values())

    print(f"\n{'endpoint':<12} {'count':>7} {'mean':>8} {'p50':>8} {'p95':>8} {'p99':>8}  (ms)")
    print("-" * 62)
    for label, values in sorted(latencies.items(), key=lambda kv: -len(kv[1])):
        print(
            f"{label:<12} {len(values):>7} {statistics.fmean(values):>8.1f} "
            f"{percentile(values, 50):>8.1f} {percentile(values, 95):>8.1f} "
            f"{percentile(values, 99):>8.1f}"
        )

    print(f"\ntotal requests: {total}  |  elapsed: {elapsed:.1f}s  |  {total / elapsed:.1f} rps")
    if errors:
        print("\nerrors:")
        for key, count in sorted(errors.items(), key=lambda kv: -kv[1]):
            print(f"  {key}: {count}")
    else:
        print("\nno errors")

    autosave_p99 = percentile(latencies.get("autosave", []), 99)
    if autosave_p99 > 200:
        print(
            f"\nWARNING: auto-save p99 is {autosave_p99:.0f} ms (target < 100 ms). "
            "Check the flush task and Redis before running a real exam."
        )

    print(f"\ndata integrity: {integrity['checked']} answers verified, {integrity['lost']} lost")
    if integrity["lost"]:
        print("lost-answer examples (first 20):")
        for line in lost_examples:
            print(f"  {line}")

    if run_wall_ms:
        print(
            f"\nrun wall time (submit -> done/error), n={len(run_wall_ms)}: "
            f"mean={statistics.fmean(run_wall_ms):.0f}ms p50={percentile(run_wall_ms, 50):.0f}ms "
            f"p95={percentile(run_wall_ms, 95):.0f}ms p99={percentile(run_wall_ms, 99):.0f}ms"
        )
        print(
            "Cross-check against GET /api/admin/judge/metrics (run_avg_queue_wait_ms, "
            "run_avg_exec_ms) to see how much of this is queueing vs. actual judge time."
        )

    if run_repeated_rpm_achieved:
        print(
            f"\nachieved requests/min per student (requested {rpm or 10.0}), "
            f"n={len(run_repeated_rpm_achieved)}: "
            f"mean={statistics.fmean(run_repeated_rpm_achieved):.1f} "
            f"p50={percentile(run_repeated_rpm_achieved, 50):.1f} "
            f"p95={percentile(run_repeated_rpm_achieved, 95):.1f}"
        )
        print(
            "Achieved < requested means judge latency exceeded 60/rpm seconds — the "
            "Run button stays disabled until the previous run completes, capping the "
            "real rate below what was asked for."
        )

    if run_language_counts:
        total_by_lang = sum(run_language_counts.values())
        print(f"\nsuccessful run_submit by language (n={total_by_lang}):")
        for lang, count in sorted(run_language_counts.items(), key=lambda kv: -kv[1]):
            print(f"  {lang:<12} {count:>6}  ({count / total_by_lang * 100:.1f}%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--students", type=int, default=100)
    parser.add_argument("--duration", type=int, default=120, help="seconds per student")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--prefix", default="STU", help="student id prefix, e.g. LT for the loadtest cohort")
    parser.add_argument(
        "--rpm", type=float, default=None, help="target requests/minute per student (tunes autosave pacing)"
    )
    parser.add_argument(
        "--exam-title",
        default=None,
        help="exact exam title to target (required when other exams are visible to the cohort, "
        "e.g. --exam-title 'Load Test Exam')",
    )
    parser.add_argument(
        "--stagger",
        type=float,
        default=60.0,
        help="max random login delay in seconds (0 = every student authenticates and starts "
        "hitting exam endpoints immediately, no ramp-up)",
    )
    parser.add_argument(
        "--mode",
        choices=["mixed", "run_spike", "run_repeated"],
        default="mixed",
        help="'mixed' (default) = autosave/heartbeat/nav/submit traffic (spec Test A; also drives "
        "Test D's grade-spike via a short --duration + --stagger 0). 'run_spike' = every student "
        "does exactly one Run and nothing else (spec Test B/C) — pair with --stagger 0 for a true "
        "simultaneous burst. 'run_repeated' = every student clicks Run at --rpm for the full "
        "--duration (not just once) — sustained Run load rather than a single spike or burst.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="skip the first N students in the pool (e.g. --offset 130 targets STU0131+) — use "
        "this to get a fresh, never-attempted range instead of colliding with a previous run's "
        "STU0001.. students on the same exam",
    )
    args = parser.parse_args()
    asyncio.run(
        main(
            args.students,
            args.duration,
            args.base_url,
            args.prefix,
            args.rpm,
            args.exam_title,
            args.stagger,
            args.mode,
            args.offset,
        )
    )
