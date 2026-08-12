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


async def simulate_student(
    client: httpx.AsyncClient,
    student_id: str,
    duration: int,
    autosave_interval: tuple[float, float] = (8.0, 12.0),
    exam_title: str | None = None,
    stagger_seconds: float = 60.0,
) -> None:
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
        return

    response = await timed(
        "magic_verify",
        client.post("/api/auth/student/magic-link/verify", json={"token": dev_token}),
    )
    if response is None or response.status_code != 200:
        return
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
        return
    exam_list = exams.json()
    # /available can return other exams the cohort is (or isn't deliberately)
    # scoped out of — never just take [0], or the load test silently exercises
    # the wrong exam (and pollutes its attempts) instead of the LT fixture.
    if exam_title:
        matches = [e for e in exam_list if e["title"] == exam_title]
        if not matches:
            return
        exam_id = matches[0]["id"]
    else:
        exam_id = exam_list[0]["id"]

    state = await timed(
        "start", client.post(f"/api/exam/{exam_id}/start", headers=headers)
    )
    if state is None or state.status_code != 200:
        return

    paper = await timed("questions", client.get("/api/exam/questions", headers=headers))
    if paper is None or paper.status_code != 200:
        return

    all_questions = [q for section in paper.json()["sections"] for q in section["questions"]]
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
) -> None:
    autosave_interval = autosave_interval_for_rpm(rpm) if rpm else (8.0, 12.0)
    limits = httpx.Limits(max_connections=students + 50, max_keepalive_connections=students)
    started = time.perf_counter()

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0, limits=limits) as client:
        await asyncio.gather(
            *(
                simulate_student(
                    client, f"{prefix}{i:04d}", duration, autosave_interval, exam_title, stagger
                )
                for i in range(1, students + 1)
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
        )
    )
