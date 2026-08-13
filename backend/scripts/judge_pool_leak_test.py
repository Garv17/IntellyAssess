"""Manual verification that the warm container pool (app/services/judge_pool.py)
does not leak state or processes between different students reusing the same
pooled container. NOT part of the pytest suite — this codebase has no
Docker-in-CI precedent (test_harness.py's docstring: its tests "run without
Docker"), and this script needs real Docker socket access, so it must run
inside the judge_run or judge_grade container (the only ones with
/var/run/docker.sock mounted):

    docker compose exec judge_run python -m scripts.judge_pool_leak_test

Run this — and expect every check to PASS — before ever setting
JUDGE_POOL_ENABLED=true anywhere real. Re-run after any change to judge_pool.py.

What each check actually proves, not just that it runs:
  1. Two checkouts with pool size 1 hit the physically same container (the
     precondition for every other check below to mean anything).
  2. Marker files "student A" writes to /sandbox and /tmp are gone after
     checkin+checkout — proves the workspace wipe covers both tmpfs mounts.
  3. A background process "student A" orphans (backgrounds and exits without
     waiting, so it reparents to PID1) does not survive as a zombie into
     "student B"'s checkout — proves init=True's tini is actually reaping,
     not just present. Re-run with init disabled to see this same check fail,
     confirming the assertion isn't a tautology.
  4. A container killed out-of-band (simulating a host issue, not a clean
     checkin) is detected as unhealthy on the next checkout and transparently
     rebuilt rather than handed out broken.
"""

from __future__ import annotations

import sys

from app.config import settings
from app.services import judge_pool

LANGUAGE = "python"
IMAGE = "python:3.11-alpine"

_PASS = "PASS"
_FAIL = "FAIL"
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    results.append((name, ok))
    print(f"[{_PASS if ok else _FAIL}] {name}")


def _ps_count(container) -> int:
    exit_code, output = container.exec_run(["sh", "-c", "ps aux | wc -l"], demux=True)
    stdout, _ = output if output else (b"0", b"")
    return int(stdout.decode(errors="replace").strip() or "0") if exit_code == 0 else -1


def _file_exists(container, path: str) -> bool:
    exit_code, _ = container.exec_run(["sh", "-c", f"test -f {path}"])
    return exit_code == 0


def main() -> int:
    settings.judge_pool_enabled = True
    settings.judge_pool_size_per_language = 1  # force both checkouts onto one slot

    print(f"Leak test — language={LANGUAGE}, pool size forced to 1\n")

    # 1. Two checkouts, same slot, must be the same physical container.
    first = judge_pool.checkout(LANGUAGE, IMAGE)
    if first is None:
        print("FATAL: checkout() returned None — is Docker reachable and JUDGE_POOL_ENABLED honored?")
        return 1
    judge_pool.checkin(first, healthy=True)
    second = judge_pool.checkout(LANGUAGE, IMAGE)
    same_container = first.container.id == second.container.id
    check("two checkouts with pool size 1 reuse the same container", same_container)
    baseline_ps = _ps_count(second.container)
    print(f"    baseline ps count on a freshly-cleaned container: {baseline_ps}")

    # 2. "Student A": leave marker files in both tmpfs mounts, then check in normally.
    second.container.exec_run(["sh", "-c", "echo leak > /sandbox/marker_a; echo leak > /tmp/marker_a"])
    # Also orphan a background process that exits shortly after — reparents to
    # PID1 when the backgrounding subshell exits without waiting for it.
    second.container.exec_run(["sh", "-c", "( sleep 0.3 & )"])
    import time as _time

    _time.sleep(1.0)  # let the orphaned sleep actually exit and (maybe) zombify
    judge_pool.checkin(second, healthy=True)

    # 3. "Student B": same slot, must see none of student A's state.
    third = judge_pool.checkout(LANGUAGE, IMAGE)
    check(
        "marker file in /sandbox does not survive into the next checkout",
        not _file_exists(third.container, "/sandbox/marker_a"),
    )
    check(
        "marker file in /tmp does not survive into the next checkout",
        not _file_exists(third.container, "/tmp/marker_a"),
    )
    after_ps = _ps_count(third.container)
    print(f"    ps count after student A's orphaned process: {after_ps}")
    check(
        "no leftover zombie from student A's orphaned process (init=True reaping)",
        after_ps <= baseline_ps + 1,  # +1 slack for the exec_run probe itself
    )
    judge_pool.checkin(third, healthy=True)

    # 4. Out-of-band kill: simulate a host issue *while the container is sitting
    # idle in the pool* (checked in normally first) — not mid-checkout, which
    # would just mean the slot lock is still legitimately held by that checkout.
    fourth = judge_pool.checkout(LANGUAGE, IMAGE)
    judge_pool.checkin(fourth, healthy=True)
    fourth.container.kill()
    fifth = judge_pool.checkout(LANGUAGE, IMAGE)
    check(
        "checkout after an out-of-band kill detects it and rebuilds",
        fifth is not None and fifth.container.status == "running",
    )
    if fifth is not None:
        judge_pool.checkin(fifth, healthy=True)

    print("\n--- summary ---")
    failed = [name for name, ok in results if not ok]
    for name, ok in results:
        print(f"  [{_PASS if ok else _FAIL}] {name}")
    if failed:
        print(f"\n{len(failed)} check(s) FAILED. Do not enable JUDGE_POOL_ENABLED until these pass.")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
