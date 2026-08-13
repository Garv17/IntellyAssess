"""Progressive load ramp: runs increasing student-count stages against the live
stack, and stops as soon as a stage's error rate crosses the failure threshold —
that's the breaking point. Each stage uses a disjoint slice of the student pool
(offset advances stage by stage) so a 409 from an already-attempted exam never
gets miscounted as a capacity failure.

Requires ENVIRONMENT=development on the server (magic-link dev_token) and a
large enough STUxxxx / cohort=loadtest student pool sitting on a published
cohort=loadtest exam — see scripts/seed.py and the admin API for setup. Needs
at least sum(stages) students starting from --offset.

    python -m scripts.ramp_loadtest --stages 25,50,100,200,500 --stage-duration 45
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx

from scripts import loadtest

FAIL_ERROR_RATE = 0.10  # a stage is a "break" if >=10% of its requests errored
FAIL_LATENCY_MS = 2000  # ...or if any endpoint's p99 exceeds this


async def run_stage(base_url: str, student_ids: list[str], duration: int, mode: str = "mixed") -> dict:
    loadtest.latencies.clear()
    loadtest.errors.clear()
    loadtest.run_wall_ms.clear()

    limits = httpx.Limits(max_connections=len(student_ids) + 50, max_keepalive_connections=len(student_ids))
    started = time.perf_counter()
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0, limits=limits) as client:
        if mode == "run_spike":
            # Test B/C at increasing scale: every student in this stage does exactly
            # one Run, no stagger — a true simultaneous burst at this stage's size.
            await asyncio.gather(
                *(loadtest.simulate_run_once(client, sid, stagger_seconds=0.0) for sid in student_ids)
            )
        else:
            await asyncio.gather(
                *(loadtest.simulate_student(client, sid, duration) for sid in student_ids)
            )
    elapsed = time.perf_counter() - started

    total = sum(len(v) for v in loadtest.latencies.values())
    failed = sum(loadtest.errors.values())
    error_rate = failed / total if total else 0.0
    worst_p99 = max(
        (loadtest.percentile(v, 99) for v in loadtest.latencies.values()), default=0.0
    )

    return {
        "students": len(student_ids),
        "elapsed": elapsed,
        "total_requests": total,
        "failed_requests": failed,
        "error_rate": error_rate,
        "worst_p99_ms": worst_p99,
        "latencies": {k: list(v) for k, v in loadtest.latencies.items()},
        "errors": dict(loadtest.errors),
        "run_wall_ms": list(loadtest.run_wall_ms),
    }


def print_stage_report(stage_n: int, result: dict) -> None:
    print(f"\n=== Stage {stage_n}: {result['students']} concurrent students ===")
    print(f"{'endpoint':<14} {'count':>7} {'mean':>8} {'p50':>8} {'p95':>8} {'p99':>8}  (ms)")
    print("-" * 64)
    for label, values in sorted(result["latencies"].items(), key=lambda kv: -len(kv[1])):
        print(
            f"{label:<14} {len(values):>7} {statistics.fmean(values):>8.1f} "
            f"{loadtest.percentile(values, 50):>8.1f} {loadtest.percentile(values, 95):>8.1f} "
            f"{loadtest.percentile(values, 99):>8.1f}"
        )
    print(
        f"\nrequests: {result['total_requests']}  |  failed: {result['failed_requests']} "
        f"({result['error_rate'] * 100:.1f}%)  |  elapsed: {result['elapsed']:.1f}s "
        f"|  worst p99: {result['worst_p99_ms']:.0f}ms"
    )
    if result["errors"]:
        print("errors:")
        for key, count in sorted(result["errors"].items(), key=lambda kv: -kv[1]):
            print(f"  {key}: {count}")
    if result["run_wall_ms"]:
        wall = result["run_wall_ms"]
        print(
            f"run wall time, n={len(wall)}: mean={statistics.fmean(wall):.0f}ms "
            f"p50={loadtest.percentile(wall, 50):.0f}ms p95={loadtest.percentile(wall, 95):.0f}ms "
            f"p99={loadtest.percentile(wall, 99):.0f}ms"
        )


def stage_failed(result: dict) -> bool:
    return result["error_rate"] >= FAIL_ERROR_RATE or result["worst_p99_ms"] >= FAIL_LATENCY_MS


async def main(stages: list[int], stage_duration: int, base_url: str, offset: int, mode: str = "mixed") -> None:
    summary: list[dict] = []
    cursor = offset

    for stage_n in stages:
        student_ids = [f"STU{i:04d}" for i in range(cursor + 1, cursor + stage_n + 1)]
        cursor += stage_n

        result = await run_stage(base_url, student_ids, stage_duration, mode)
        print_stage_report(stage_n, result)
        summary.append({"stage": stage_n, **result})

        if stage_failed(result):
            print(
                f"\n*** BREAKING POINT: stage at {stage_n} concurrent students exceeded "
                f"{FAIL_ERROR_RATE * 100:.0f}% error rate or {FAIL_LATENCY_MS}ms p99. "
                "Stopping ramp. ***"
            )
            break
    else:
        print(f"\nNo breaking point found up to {stages[-1]} concurrent students.")

    print("\n" + "=" * 64)
    print(f"{'stage':>8} {'requests':>10} {'failed':>8} {'error %':>9} {'worst p99':>11}  verdict")
    print("-" * 64)
    for s in summary:
        verdict = "FAIL" if stage_failed(s) else "pass"
        print(
            f"{s['stage']:>8} {s['total_requests']:>10} {s['failed_requests']:>8} "
            f"{s['error_rate'] * 100:>8.1f}% {s['worst_p99_ms']:>10.0f}ms  {verdict}"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--stages", default="25,50,100,200,500", help="comma-separated student counts")
    parser.add_argument("--stage-duration", type=int, default=45, help="active seconds per student per stage")
    parser.add_argument("--base-url", default=loadtest.BASE_URL)
    parser.add_argument("--offset", type=int, default=0, help="skip the first N students in the pool")
    parser.add_argument(
        "--mode",
        choices=["mixed", "run_spike"],
        default="mixed",
        help="'run_spike' ramps Test B/C (simultaneous Run bursts) instead of the default "
        "autosave/heartbeat/nav/submit mix",
    )
    args = parser.parse_args()
    stage_list = [int(s) for s in args.stages.split(",")]
    asyncio.run(main(stage_list, args.stage_duration, args.base_url, args.offset, args.mode))
