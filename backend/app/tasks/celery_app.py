from celery import Celery
from celery.signals import (
    after_setup_logger,
    after_setup_task_logger,
    task_failure,
    task_postrun,
    task_prerun,
    worker_ready,
    worker_shutdown,
)

from app.config import settings
from app.logging_config import (
    bind_request_context,
    clear_request_context,
    get_logger,
    setup_logging,
)

log = get_logger("app.tasks.celery")

celery_app = Celery(
    "exam_platform",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks.ai_tasks", "app.tasks.maintenance", "app.tasks.mailer_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=180,
    task_soft_time_limit=150,
    # AI evaluation gets its own queue: a call to OpenAI blocks a worker slot for
    # seconds, and a burst of submissions must never delay the answer flush or the
    # auto-submit sweeper, both of which are exam-critical.
    task_routes={
        "ai.*": {"queue": "ai"},
        "maintenance.*": {"queue": "default"},
        "mailer.*": {"queue": "default"},
    },
    beat_schedule={
        "flush-answer-buffer": {
            "task": "maintenance.flush_answers",
            "schedule": float(settings.autosave_flush_seconds),
        },
        "auto-submit-expired": {
            "task": "maintenance.auto_submit_expired",
            "schedule": float(settings.autosubmit_sweep_seconds),
        },
    },
)


# --------------------------------------------------------------------- logging
#
# Celery installs its own root handler unless told otherwise. These two signals
# hand it ours instead, which is what makes worker output match the web tier's
# format — previously `logging.basicConfig` in app.main never ran here at all,
# because workers only import the task modules.


@after_setup_logger.connect
@after_setup_task_logger.connect
def _configure_celery_logging(logger=None, **_kwargs) -> None:
    setup_logging(force=True)
    if logger is not None:
        # Let records bubble to the root handler installed above rather than
        # being emitted twice by Celery's own.
        logger.handlers = []
        logger.propagate = True


@worker_ready.connect
def _on_worker_ready(sender=None, **_kwargs) -> None:
    log.info(
        "celery worker ready",
        extra={"hostname": getattr(sender, "hostname", None)},
    )


@worker_shutdown.connect
def _on_worker_shutdown(sender=None, **_kwargs) -> None:
    log.info("celery worker shutting down", extra={"hostname": getattr(sender, "hostname", None)})


# ----------------------------------------------------------------- task tracing
#
# The task ID goes into the same correlation slot the HTTP middleware uses, so
# every line a task emits — including ones from shared service modules — carries
# it, and an AI evaluation can be traced back from the log to the submission
# that queued it.


@task_prerun.connect
def _on_task_prerun(task_id=None, task=None, **_kwargs) -> None:
    bind_request_context(request_id=str(task_id)[:16] if task_id else None)
    log.info("task started", extra={"task_name": getattr(task, "name", None), "task_id": task_id})


@task_postrun.connect
def _on_task_postrun(task_id=None, task=None, state=None, **_kwargs) -> None:
    log.info(
        "task finished",
        extra={"task_name": getattr(task, "name", None), "task_id": task_id, "state": state},
    )
    clear_request_context()


@task_failure.connect
def _on_task_failure(task_id=None, exception=None, sender=None, einfo=None, **_kwargs) -> None:
    log.error(
        "task failed",
        extra={
            "task_name": getattr(sender, "name", None),
            "task_id": task_id,
            "error_type": type(exception).__name__ if exception else None,
            "error": str(exception) if exception else None,
            "traceback": str(einfo) if einfo else None,
        },
    )
