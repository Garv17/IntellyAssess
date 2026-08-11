from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "IntellyAssess"
    environment: str = "development"
    debug: bool = True

    # Postgres — asyncpg URL for the app, psycopg URL for Alembic/Celery
    database_url: str = "postgresql+asyncpg://exam:exam@localhost:5432/exam"
    database_url_sync: str = "postgresql+psycopg://exam:exam@localhost:5432/exam"
    db_pool_size: int = 20
    db_max_overflow: int = 10

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # JWT
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    # Magic-link email (Brevo transactional API)
    brevo_api_key: str = ""
    brevo_sender_email: str = "no-reply@example.com"
    brevo_sender_name: str = "IntellyAssess"
    frontend_base_url: str = "http://localhost:5180"
    magic_link_ttl_minutes: int = 15
    # Minimum gap between two link requests for the same student — protects the
    # Brevo daily send quota from a student mashing "resend".
    magic_link_cooldown_seconds: int = 60

    # Exam behaviour
    # This is a single batched Celery-beat job (up to 500 dirty attempts per run),
    # not a per-student request, so lowering it doesn't add per-user server load —
    # it only bounds how much buffered Redis data could be lost if Redis crashes
    # uncleanly. 15s keeps that exposure small without adding meaningful DB writes.
    autosave_flush_seconds: int = 15
    autosubmit_sweep_seconds: int = 30
    heartbeat_seconds: int = 30
    # Grace period for in-flight requests that started just before the deadline.
    deadline_grace_seconds: int = 5

    # Judge sandbox
    judge_image_prefix: str = "exam-judge"
    judge_timeout_seconds: int = 5
    judge_memory_mb: int = 256
    judge_cpus: float = 1.0
    judge_pids_limit: int = 64
    judge_max_code_bytes: int = 64 * 1024

    # Uploads
    upload_dir: str = "./uploads"
    max_upload_mb: int = 10

    cors_origins: list[str] = ["http://localhost:5180"]


WEAK_SECRET_DEFAULT = "change-me-in-production"
MIN_SECRET_BYTES = 32  # RFC 7518 §3.2 minimum for HS256


@lru_cache
def get_settings() -> Settings:
    config = Settings()
    if config.environment != "development":
        # Refuse to boot on a guessable signing key. A forged token here means a
        # forged exam submission, so this fails loudly rather than warning.
        if config.jwt_secret == WEAK_SECRET_DEFAULT:
            raise RuntimeError(
                "JWT_SECRET is still the default value. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        if len(config.jwt_secret.encode()) < MIN_SECRET_BYTES:
            raise RuntimeError(
                f"JWT_SECRET must be at least {MIN_SECRET_BYTES} bytes for HS256 "
                f"(got {len(config.jwt_secret.encode())})"
            )
        if not config.brevo_api_key:
            # Students can only sign in via magic link, so a missing mailer key means
            # nobody can log in at all — fail at boot, not on the first login attempt.
            raise RuntimeError("BREVO_API_KEY is not set; student magic-link login cannot send mail")
    return config


settings = get_settings()
