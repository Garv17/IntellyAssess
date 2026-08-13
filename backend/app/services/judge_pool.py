"""Warm container pool for the judge sandbox — disabled by default
(settings.judge_pool_enabled), see SCALING.md before turning it on anywhere real.

Every "Run"/grade execution used to cost a full container create/start/exec/remove
cycle (~8s average under load, almost entirely lifecycle overhead, not compute).
This keeps a fixed number of long-lived containers per language warm instead,
checked out/in like a connection pool. It intentionally knows nothing about
LanguageSpec/compile_cmd/run_cmd — sandbox.py owns judging semantics, this module
owns container lifecycle, coordinated by name+image alone to avoid a circular
import between the two.

Safety model: reusing a container across different students' code is only as
safe as (a) the workspace being provably wiped between uses and (b) no process
surviving from one use into the next. (a) is a plain `rm -rf` on the two tmpfs
mounts. (b) is Docker's `init=True` flag, which injects `tini` as the real PID1 —
unlike this module's own `sleep` command (which never calls wait()), tini reaps
any orphaned descendant, which is exactly the gap that made reuse unsafe before.
Every other security control (network_disabled, read_only, cap_drop, pids_limit,
non-root user) is unchanged from the ephemeral path.

Checkout/checkin is coordinated through the same synchronous Redis client Celery
tasks already use (app.sync_db.sync_redis — app.cache's client is async and can't
cross prefork worker-process boundaries), using the same SET-NX-EX lock idiom as
app.cache.claim_run_cooldown/start_magic_cooldown.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from app.config import settings
from app.sync_db import sync_redis

log = logging.getLogger(__name__)

# A freshly-cleaned pooled container's PID1 (tini) + the ps invocation itself is
# 2-3 lines of `ps aux` output. This is a heuristic ceiling, not an exact
# baseline — comfortably above normal idle, comfortably below what a genuine
# leaked batch of processes would produce.
_MAX_IDLE_PROCESSES = 8

_CLEANUP_CMD = [
    "sh",
    "-c",
    "rm -rf -- /sandbox/* /sandbox/.[!.]* /sandbox/..?* "
    "/tmp/* /tmp/.[!.]* /tmp/..?* 2>/dev/null; true",
]


@dataclass
class PooledContainer:
    container: "docker.models.containers.Container"
    language: str
    slot: int


def _client() -> docker.DockerClient:
    return docker.from_env()


def _slot_name(language: str, idx: int) -> str:
    return f"judge-pool-{language}-{idx}"


def _lock_key(language: str, idx: int) -> str:
    return f"judge:pool:{language}:slot:{idx}"


def _ensure_image(client: docker.DockerClient, image: str) -> None:
    try:
        client.images.get(image)
    except ImageNotFound:
        log.info("judge_pool: pulling image %s", image)
        client.images.pull(image)


def _create(client: docker.DockerClient, image: str, name: str) -> "docker.models.containers.Container":
    return client.containers.create(
        image,
        # Lives indefinitely (reused across many executions), unlike the ephemeral
        # path's per-session bounded sleep. The numeric form avoids relying on
        # BusyBox `sleep` supporting the literal "infinity" argument.
        command=["sleep", "2147483647"],
        name=name,
        # Injects tini as real PID1 so orphaned descendants get reaped between
        # reuses — see module docstring. Independent of every other security flag.
        init=True,
        entrypoint=[],
        working_dir="/sandbox",
        network_disabled=True,
        network_mode="none",
        read_only=True,
        tmpfs={
            "/sandbox": "rw,exec,size=32m,mode=1777",
            "/tmp": "rw,size=16m,mode=1777",
        },
        mem_limit=f"{settings.judge_pool_memory_mb}m",
        memswap_limit=f"{settings.judge_pool_memory_mb}m",
        nano_cpus=int(settings.judge_cpus * 1e9),
        pids_limit=settings.judge_pids_limit,
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        user="1000:1000",
        detach=True,
        auto_remove=False,
    )


def _get_or_create(language: str, image: str, idx: int) -> "docker.models.containers.Container":
    client = _client()
    name = _slot_name(language, idx)
    _ensure_image(client, image)
    try:
        container = _create(client, image, name)
    except APIError as exc:
        # A container surviving under this name from before a redeploy — Redis has
        # no memory of it, but Docker does. Adopt it; the health check below will
        # discard and rebuild if it turns out to be unusable.
        if exc.status_code == 409:
            container = client.containers.get(name)
        else:
            raise
    container.reload()
    if container.status != "running":
        container.start()
    return container


def _check_health(container: "docker.models.containers.Container") -> bool:
    try:
        container.reload()
    except NotFound:
        return False
    if container.status != "running":
        return False
    try:
        exit_code, output = container.exec_run(["sh", "-c", "ps aux | wc -l"], demux=True)
    except DockerException:
        return False
    if exit_code != 0:
        return False
    stdout, _stderr = output if output else (b"", b"")
    try:
        count = int((stdout or b"0").decode(errors="replace").strip())
    except ValueError:
        return False
    return count <= _MAX_IDLE_PROCESSES


def _release_lock(language: str, idx: int) -> None:
    try:
        sync_redis.delete(_lock_key(language, idx))
    except Exception:
        log.warning("judge_pool: failed to release slot lock %s/%s", language, idx, exc_info=True)


def checkout(language: str, image: str) -> PooledContainer | None:
    """Claims an idle, healthy container for this language, creating/rebuilding it
    if needed. Returns None if pooling is disabled, every slot is in use, or Redis/
    Docker is unavailable — the caller (sandbox.execute) must fall back to the
    ephemeral create/destroy path in every None case; this never blocks."""
    if not settings.judge_pool_enabled:
        return None

    size = settings.judge_pool_size_per_language
    order = list(range(size))
    random.shuffle(order)  # spread load across slots rather than always favoring slot 0

    for idx in order:
        key = _lock_key(language, idx)
        try:
            claimed = sync_redis.set(key, "1", nx=True, ex=settings.judge_pool_slot_ttl_seconds)
        except Exception:
            log.warning("judge_pool: redis unavailable for checkout", exc_info=True)
            return None
        if not claimed:
            continue

        try:
            container = _get_or_create(language, image, idx)
            if not _check_health(container):
                try:
                    container.remove(force=True)
                except DockerException:
                    log.warning("judge_pool: failed to remove unhealthy container", exc_info=True)
                container = _get_or_create(language, image, idx)
            return PooledContainer(container=container, language=language, slot=idx)
        except DockerException:
            log.exception("judge_pool: failed to prepare slot %s for %s", idx, language)
            _release_lock(language, idx)
            return None

    return None  # every slot locked — caller falls back to the ephemeral path


def checkin(pooled: PooledContainer, healthy: bool) -> None:
    """Releases a checked-out container. If the caller reports it healthy, wipes
    the workspace and re-verifies before trusting it for the next checkout;
    anything unhealthy is discarded so the next checkout rebuilds it fresh under
    the same slot name. Always releases the lock, even if cleanup itself fails."""
    try:
        if healthy:
            try:
                exit_code, _output = pooled.container.exec_run(_CLEANUP_CMD, workdir="/", demux=True)
                healthy = exit_code == 0 and _check_health(pooled.container)
            except DockerException:
                healthy = False
        if not healthy:
            try:
                pooled.container.remove(force=True)
            except DockerException:
                log.warning("judge_pool: failed to remove discarded container", exc_info=True)
    finally:
        _release_lock(pooled.language, pooled.slot)
