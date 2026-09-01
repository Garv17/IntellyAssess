from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "IntellyAssess"
    environment: str = "development"
    debug: bool = True

    # Logging. "json" is the shippable format used everywhere but a developer's
    # terminal; "console" is the readable one. log_sql is deliberately separate
    # from log_level because SQL echo at INFO would bury every other line.
    log_level: str = "INFO"
    log_format: str = "json"
    log_sql: bool = False
    # "live snapshot built" fires on every Live Monitor rebuild — every answer-
    # flush cycle for every exam with activity, so on a busy exam day it is by
    # far the highest-volume line in the system. Off by default so it doesn't
    # bury the events an admin tailing logs during a live exam actually wants
    # (logins, submissions, auto-submits); flip it on when the thing you're
    # diagnosing is "the dashboard itself is lagging or stuck".
    log_live_monitor: bool = False

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

    # Coding submissions. This build is submission-only: student code is never
    # executed anywhere, it is persisted and graded by an AI evaluator + a human.
    submission_max_code_bytes: int = 64 * 1024

    # AI evaluation (OpenAI-compatible chat completions with structured output).
    # The key is read from the environment only — never checked in, never sent
    # to the frontend.
    ai_evaluation_enabled: bool = True
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: float = 60.0
    # Counts against OpenAI's per-minute token limit whether or not it is used, so
    # this trades queue drain rate against the risk of truncating a long
    # evaluation. Measured responses are ~450 tokens.
    openai_max_output_tokens: int = 1500
    # Path to the rubric the evaluator scores against. Relative paths resolve
    # from the backend/ directory (the Docker image's WORKDIR).
    coding_rubric_path: str = "assessments/rubrics/default_coding.yaml"
    coding_evaluator_prompt_path: str = "ai/prompts/coding_evaluator.md"
    # Below this, the submission is flagged "Manual Review Recommended". A review
    # signal only — the model's confidence is not a calibrated probability.
    ai_low_confidence_threshold: float = 0.6

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
        if config.debug:
            # FastAPI's debug mode returns tracebacks to the caller, which on this
            # app means leaking query text and connection details to a student.
            raise RuntimeError("DEBUG must be false outside development")
        if "*" in config.cors_origins:
            # A wildcard origin with allow_credentials=True lets any site drive the
            # API using a logged-in examiner's cookies.
            raise RuntimeError("CORS_ORIGINS cannot be '*' when credentials are allowed")
        if config.ai_evaluation_enabled and not config.openai_api_key:
            # Otherwise every coding submission in the sitting lands on AI_FAILED and
            # the whole cohort needs hand-marking — discovered one exam too late.
            raise RuntimeError(
                "AI_EVALUATION_ENABLED is true but OPENAI_API_KEY is empty. Set the key, "
                "or set AI_EVALUATION_ENABLED=false to mark coding answers by hand."
            )
    return config


settings = get_settings()
