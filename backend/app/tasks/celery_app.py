from celery import Celery

from app.config import settings

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
