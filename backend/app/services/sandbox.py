"""Docker-backed code execution sandbox.

Every run is a throwaway container with: no network, read-only rootfs, capped
memory/CPU/pids, a non-root user, and a hard wall-clock kill. Untrusted student
code is assumed hostile — the container is the only boundary we rely on.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass

import docker
from docker.errors import DockerException, ImageNotFound

from app.config import settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LanguageSpec:
    image: str
    filename: str
    # Compile step is optional; run_cmd executes inside /sandbox with stdin piped in.
    compile_cmd: list[str] | None
    run_cmd: list[str]
    # SQL only: there's no separate "program" to run against varying input — a test
    # case's stdin *is* the schema/setup script, and the student's code is the query
    # to run against it. Both get concatenated into one script per test case instead
    # of code being compiled once and stdin varying alone.
    combine_stdin_with_code: bool = False


LANGUAGES: dict[str, LanguageSpec] = {
    "python": LanguageSpec(
        image="python:3.11-alpine",
        filename="main.py",
        compile_cmd=None,
        run_cmd=["python", "main.py"],
    ),
    "javascript": LanguageSpec(
        image="node:20-alpine",
        filename="main.js",
        compile_cmd=None,
        run_cmd=["node", "main.js"],
    ),
    "c": LanguageSpec(
        image="gcc:13",
        filename="main.c",
        compile_cmd=["gcc", "-O2", "-std=c17", "-o", "main", "main.c"],
        run_cmd=["./main"],
    ),
    "cpp": LanguageSpec(
        image="gcc:13",
        filename="main.cpp",
        compile_cmd=["g++", "-O2", "-std=c++17", "-o", "main", "main.cpp"],
        run_cmd=["./main"],
    ),
    "java": LanguageSpec(
        image="eclipse-temurin:21-jdk-alpine",
        filename="Main.java",
        compile_cmd=["javac", "Main.java"],
        run_cmd=["java", "Main"],
    ),
    "sql": LanguageSpec(
        # A dedicated sqlite3-CLI image — no server process, fits the same
        # ephemeral-container-per-run model as everything else here.
        image="nouchka/sqlite3:latest",
        filename="main.sql",
        compile_cmd=None,
        run_cmd=["sqlite3", "-batch", ":memory:"],
        combine_stdin_with_code=True,
    ),
}


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    time_ms: int
    timed_out: bool
    verdict: str  # accepted | wrong_answer | tle | runtime_error | compile_error | internal_error


MAX_OUTPUT_CHARS = 10_000


def _client() -> docker.DockerClient:
    return docker.from_env()


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n... [output truncated]"


def normalize(text: str) -> str:
    """Trailing-whitespace-insensitive comparison. Students should not lose marks for
    a stray newline, but internal spacing still matters."""
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def execute(
    language: str,
    code: str,
    stdin_batch: list[str],
    time_limit_ms: int,
    memory_limit_mb: int,
) -> tuple[list[RunResult], str | None]:
    """Compile once, then run against each stdin. Returns (results, compile_error)."""
    spec = LANGUAGES.get(language)
    if spec is None:
        return [], f"Unsupported language: {language}"
    if len(code.encode()) > settings.judge_max_code_bytes:
        return [], "Submission exceeds the size limit"

    try:
        client = _client()
    except DockerException as exc:
        log.error("docker unavailable: %s", exc)
        return [], "Judge is temporarily unavailable"

    container = None
    try:
        try:
            client.images.get(spec.image)
        except ImageNotFound:
            log.info("pulling judge image %s", spec.image)
            client.images.pull(spec.image)

        container = client.containers.create(
            spec.image,
            command=["sleep", str(max(30, len(stdin_batch) * (time_limit_ms // 1000 + 2)))],
            # Some judge images set their own ENTRYPOINT (e.g. the sqlite3 image
            # defaults to running sqlite3 itself). Clearing it guarantees `command`
            # above is what actually runs as PID 1, regardless of the base image.
            entrypoint=[],
            working_dir="/sandbox",
            network_disabled=True,
            network_mode="none",
            read_only=True,
            # /sandbox must be writable for compilation output, but is capped and
            # destroyed with the container. mode=1777 so the non-root user can write.
            # exec is required here (unlike /tmp) — compiled languages (C/C++) run a
            # binary straight out of this mount; Docker's tmpfs default is noexec.
            tmpfs={
                "/sandbox": "rw,exec,size=32m,mode=1777",
                "/tmp": "rw,size=16m,mode=1777",
            },
            mem_limit=f"{max(memory_limit_mb, settings.judge_memory_mb)}m",
            memswap_limit=f"{max(memory_limit_mb, settings.judge_memory_mb)}m",
            nano_cpus=int(settings.judge_cpus * 1e9),
            pids_limit=settings.judge_pids_limit,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            user="1000:1000",
            detach=True,
            auto_remove=False,
        )
        container.start()

        # Docker's put_archive API refuses any container created with read_only=True,
        # even when the target path is a writable tmpfs. Writing the file through exec
        # instead is an ordinary write to that tmpfs, so it succeeds. base64 keeps
        # arbitrary source bytes (quotes, newlines, unicode) intact through the shell.
        encoded = base64.b64encode(code.encode()).decode()
        write_code, write_out = container.exec_run(
            ["sh", "-c", f'printf %s "$SANDBOX_CODE" | base64 -d > /sandbox/{spec.filename}'],
            environment={"SANDBOX_CODE": encoded},
            workdir="/sandbox",
            demux=True,
        )
        if write_code != 0:
            _, stderr = write_out if write_out else (b"", b"")
            log.error("failed to stage source: %s", (stderr or b"").decode(errors="replace"))
            return [], "Judge failed to stage the submission"

        if spec.compile_cmd:
            exit_code, output = container.exec_run(
                spec.compile_cmd, workdir="/sandbox", demux=True
            )
            if exit_code != 0:
                stdout, stderr = output
                # Diagnostics normally land on stderr, but fall back to stdout rather
                # than lose the message if a toolchain logs errors there instead.
                message = (stderr or b"").decode(errors="replace") or (stdout or b"").decode(
                    errors="replace"
                )
                return [], _truncate(message) or "Compilation failed"

        wall_limit = max(1.0, time_limit_ms / 1000)
        results: list[RunResult] = []

        for stdin_text in stdin_batch:
            started = time.perf_counter()
            effective_stdin = f"{stdin_text}\n{code}\n" if spec.combine_stdin_with_code else stdin_text
            # `timeout` inside the container enforces the per-case limit; the container's
            # own sleep command bounds the total session as a second line of defence.
            command = [
                "sh",
                "-c",
                f"printf '%s' \"$SANDBOX_STDIN\" | timeout -s KILL {wall_limit:.1f} "
                + " ".join(spec.run_cmd),
            ]
            exit_code, output = container.exec_run(
                command,
                workdir="/sandbox",
                demux=True,
                environment={"SANDBOX_STDIN": effective_stdin},
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            stdout_b, stderr_b = output if output else (b"", b"")
            stdout = _truncate((stdout_b or b"").decode(errors="replace"))
            stderr = _truncate((stderr_b or b"").decode(errors="replace"))

            # `timeout -s KILL` reports 137; treat it as TLE rather than a crash.
            timed_out = exit_code == 137 or elapsed_ms > time_limit_ms * 2
            verdict = "tle" if timed_out else ("runtime_error" if exit_code != 0 else "ok")

            results.append(
                RunResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    time_ms=elapsed_ms,
                    timed_out=timed_out,
                    verdict=verdict,
                )
            )

        return results, None

    except DockerException as exc:
        log.exception("judge execution failed")
        return [], f"Judge error: {exc}"
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except DockerException:
                log.warning("failed to remove judge container", exc_info=True)
