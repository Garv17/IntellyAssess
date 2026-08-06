"""Load test that mirrors the real traffic mix rather than hammering one endpoint.

Each simulated student logs in, starts the exam, then loops: auto-save every ~10 s,
heartbeat every ~30 s, navigation every ~45 s. That ratio is what actually decides
whether the platform holds, since auto-save dominates the request budget.

    python -m scripts.loadtest --students 400 --duration 300
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
PASSWORD = "Student@123"

latencies: dict[str, list[float]] = defaultdict(list)
errors: dict[str, int] = defaultdict(int)


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


async def simulate_student(client: httpx.AsyncClient, student_id: str, duration: int) -> None:
    # Stagger logins over 60 s — a 400-student instant login burst is not the realistic
    # case and would measure the wrong thing.
    await asyncio.sleep(random.uniform(0, 60))

    response = await timed(
        "login",
        client.post(
            "/api/auth/student/login", json={"student_id": student_id, "password": PASSWORD}
        ),
    )
    if response is None or response.status_code != 200:
        return
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    exams = await timed("available", client.get("/api/exam/available", headers=headers))
    if exams is None or exams.status_code != 200 or not exams.json():
        return
    exam_id = exams.json()[0]["id"]

    state = await timed(
        "start", client.post(f"/api/exam/{exam_id}/start", headers=headers)
    )
    if state is None or state.status_code != 200:
        return

    paper = await timed("questions", client.get("/api/exam/questions", headers=headers))
    if paper is None or paper.status_code != 200:
        return

    questions = [
        (q["id"], [o["id"] for o in q["options"]])
        for section in paper.json()["sections"]
        for q in section["questions"]
        if q["options"]
    ]
    if not questions:
        return

    deadline = time.monotonic() + duration
    last_heartbeat = 0.0
    last_nav = 0.0

    while time.monotonic() < deadline:
        question_id, option_ids = random.choice(questions)
        await timed(
            "autosave",
            client.patch(
                "/api/exam/answers",
                headers=headers,
                json={
                    "answers": [
                        {"question_id": question_id, "selected_option_id": random.choice(option_ids)}
                    ]
                },
            ),
        )

        now = time.monotonic()
        if now - last_heartbeat > 30:
            last_heartbeat = now
            await timed("heartbeat", client.post("/api/exam/heartbeat", headers=headers))
        if now - last_nav > 45:
            last_nav = now
            await timed("state", client.get("/api/exam/state", headers=headers))

        await asyncio.sleep(random.uniform(8, 12))

    await timed("submit", client.post("/api/exam/submit", headers=headers))


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * p / 100))
    return ordered[index]


async def main(students: int, duration: int, base_url: str) -> None:
    limits = httpx.Limits(max_connections=students + 50, max_keepalive_connections=students)
    started = time.perf_counter()

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0, limits=limits) as client:
        await asyncio.gather(
            *(
                simulate_student(client, f"STU{i:04d}", duration)
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--students", type=int, default=100)
    parser.add_argument("--duration", type=int, default=120, help="seconds per student")
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    asyncio.run(main(args.students, args.duration, args.base_url))
