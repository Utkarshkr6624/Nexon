"""Application configuration.

Every runtime setting is read from environment variables (optionally sourced
from a local ``.env`` file). This module is the single source of truth; no
other module reads ``os.environ`` directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "test", "production"]

#: Placeholder used when no SECRET_KEY is supplied. The validator below refuses
#: to start a production process with this value.
INSECURE_DEV_SECRET_KEY = "dev-insecure-change-me"


class Settings(BaseSettings):
    """Typed application settings."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Application ---------------------------------------------------------
    app_name: str = "NEXUS"
    app_version: str = "0.1.0"
    app_description: str = (
        "NEXUS — Personal Intelligence & Decision Platform. "
        "A local-first system for projects, planning, knowledge, analytics and ML."
    )
    environment: Environment = "development"
    debug: bool = True

    openapi_url: str = "/openapi.json"
    docs_url: str = "/docs"
    redoc_url: str = "/redoc"
    api_v1_prefix: str = "/api/v1"

    # -- Security ------------------------------------------------------------
    secret_key: str = INSECURE_DEV_SECRET_KEY
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    # Shortest password the API will store. Enforced at the schema layer, so a
    # weak password is rejected before it ever reaches the database.
    password_min_length: int = 8
    # Lifetime of a password-reset token; short because the token is a bearer
    # credential for a full account takeover.
    password_reset_expire_minutes: int = 30
    # Hard ceiling on the age of a session row, independent of token rotation.
    # Without it a user who never logs out could keep rotating refresh tokens
    # indefinitely, so a session would outlive any actual sign-in event.
    session_absolute_lifetime_days: int = 30
    # Concurrent live sessions allowed per user. On login beyond the cap the
    # oldest session is revoked, so a stolen credential cannot be used to
    # accumulate access.
    max_active_sessions: int = 20
    # How long audit rows are kept. Enforcement is NOT part of this phase — no
    # pruning job exists yet, so this states the policy and gives the value
    # something to display, but nothing is currently deleting rows.
    audit_log_retention_days: int = 400

    # -- CORS ----------------------------------------------------------------
    # Stored as a comma-separated string so it is pleasant to set in .env.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # -- Database ------------------------------------------------------------
    # An explicit URL wins; otherwise it is assembled from the POSTGRES_* parts.
    database_url: str | None = None
    test_database_url: str | None = None
    postgres_user: str = "nexus"
    postgres_password: str = "nexus"
    postgres_db: str = "nexus"
    # 127.0.0.1 rather than "localhost": on Windows the latter resolves to ::1
    # first and psycopg's async driver only connects over IPv4, so the app would
    # hang instead of failing fast.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432

    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    # Wall-clock budget for the health probe. db_pool_timeout only caps the
    # wait for a free connection, not the TCP/TLS handshake that follows, so
    # this is the only bound that keeps a filtered port from hanging the probe
    # until the OS gives up.
    db_probe_timeout_seconds: float = 3.0

    # -- Logging -------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    log_file: str | None = None
    log_request_body: bool = False
    slow_request_ms: int = 1000

    # -- Derived values ------------------------------------------------------

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlalchemy_database_uri(self) -> str:
        """SQLAlchemy async URL for the primary database."""
        if self.database_url:
            return self.database_url
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def test_sqlalchemy_database_uri(self) -> str:
        """SQLAlchemy async URL for the pytest database."""
        if self.test_database_url:
            return self.test_database_url
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        return (
            f"postgresql+psycopg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}_test"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        """Parsed CORS origins, empty strings removed."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_testing(self) -> bool:
        return self.environment == "test"

    # -- Validation ----------------------------------------------------------

    @model_validator(mode="after")
    def _reject_insecure_production_secrets(self) -> Settings:
        if self.is_production:
            if self.secret_key == INSECURE_DEV_SECRET_KEY:
                raise ValueError(
                    "SECRET_KEY must be set to a strong random value when ENVIRONMENT=production."
                )
            if self.debug:
                raise ValueError("DEBUG must be false when ENVIRONMENT=production.")
        return self

    @model_validator(mode="after")
    def _normalise_log_file(self) -> Settings:
        if self.log_file is not None and not self.log_file.strip():
            self.log_file = None
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
