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

    # -- Planner (Phase 4) ---------------------------------------------------
    # IANA zone used when a request does not pass `tz`. Every stored instant is
    # UTC; this only decides which *day boundaries* a planner view spans.
    planner_default_timezone: str = "UTC"
    # Fallback working window for a user with no availability rules, in hours
    # local to that zone. 08:00-20:00 is a daytime window, not a working-hours
    # claim: it is what the scheduler assumes when it has been told nothing.
    planner_day_start_hour: int = 8
    planner_day_end_hour: int = 20
    # Shortest block the scheduler will propose. Below this a "session" is a
    # rounding error that costs a context switch for no useful work.
    planner_min_session_minutes: int = 15
    # Longest single block. Past this the scheduler splits the work instead of
    # proposing one block nobody will sit through.
    planner_max_session_minutes: int = 240
    # Cap per task, so one large task cannot fill the entire horizon ahead of a
    # task that is due tomorrow.
    planner_max_suggestions_per_task: int = 3
    # How far forward the scheduler will search. Bounded so a request over a
    # large backlog stays a bounded walk of availability rather than a scan.
    planner_lookahead_days: int = 30

    # -- Analytics (Phase 6) ------------------------------------------------
    # The productivity weights. They live here, as one block with one validator
    # below, rather than as literals inside the scoring functions: a score whose
    # formula is spread across the code cannot be argued with — changing it means
    # finding every literal, and nothing records that the total must stay 100.
    # `_validate_productivity_weights` is what turns "the weights must sum to
    # 100" from a comment into an invariant the process cannot start without.
    analytics_productivity_weight_completion: float = 30.0
    analytics_productivity_weight_deadline: float = 25.0
    analytics_productivity_weight_consistency: float = 20.0
    analytics_productivity_weight_focus: float = 25.0
    # Period lengths the API offers for period-over-period comparison, as a
    # comma-separated string so it is pleasant to set in .env (the same reason
    # `cors_origins` is one). Parsed by `analytics_comparison_window_days`,
    # which is what the code reads.
    analytics_comparison_windows: str = "7,30,90"
    # Default window when a request names no dates. A week is the shortest span
    # that can distinguish a habit from a one-off.
    analytics_default_range_days: int = 7
    # Hard ceiling on any requested range, in days. A range query with no bound
    # is the one shape this system's indexes cannot serve: every aggregate below
    # scans the owner's whole history. 366 is a leap year, which is the longest
    # span a "compare my years" view could legitimately want.
    analytics_max_range_days: int = 366
    # Ceiling on `POST /analytics/rebuild`, which is the *write* path: it
    # recomputes each day from the underlying rows and upserts. Half a year is
    # already the most a synchronously-served request should ever be asked for;
    # anything longer belongs to the worker this phase only prepares for.
    analytics_rebuild_max_days: int = 180

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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def analytics_productivity_weights(self) -> dict[str, float]:
        """The four productivity weights, as one map.

        ``computed_field`` rather than a plain field so there is exactly one
        place the numbers are written and the scoring functions receive a
        mapping instead of four positional arguments that can be swapped in
        silence. The sum-to-100 rule is enforced by
        :meth:`_validate_productivity_weights` below, which runs before this can
        ever be read.
        """
        return {
            "completion": self.analytics_productivity_weight_completion,
            "deadline": self.analytics_productivity_weight_deadline,
            "consistency": self.analytics_productivity_weight_consistency,
            "focus": self.analytics_productivity_weight_focus,
        }

    @computed_field  # type: ignore[prop-decorator]
    @property
    def analytics_comparison_window_days(self) -> list[int]:
        """``analytics_comparison_windows`` parsed into usable day counts.

        Unparsable entries are dropped rather than raising: this feeds a list of
        suggested period lengths, and a typo in one of them should cost the user
        that suggestion rather than stop the process from booting. An empty
        result is still honest — it renders as "no comparison periods" instead of
        as a fabricated default.
        """
        windows: list[int] = []
        for part in self.analytics_comparison_windows.split(","):
            candidate = part.strip()
            if candidate.isdigit() and int(candidate) > 0:
                windows.append(int(candidate))
        return windows

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
    def _validate_productivity_weights(self) -> Settings:
        """Refuse weights that do not describe a 0-100 score.

        The productivity score is presented as a percentage, so its weights are
        the denominators of that percentage: a set summing to 90 would report a
        "80/100" that is really "80/90", and one summing to 120 would report a
        score of 100 having awarded 120 points. Neither is a tuning choice, both
        are a broken scale, and a silently-normalised third option — rescale them
        here — would hide that the configured numbers were wrong.

        So this **raises**, and the failure is loud: ``Settings`` is constructed
        once at import time via :func:`get_settings`, so the process refuses to
        start rather than serving analytics whose formula does not add up. The
        operator sees the offending values and the required total.
        """
        total = sum(self.analytics_productivity_weights.values())
        if abs(total - 100.0) > 1e-6:
            raise ValueError(
                "The analytics productivity weights must sum to 100; "
                f"they sum to {total} "
                f"(analytics_productivity_weight_completion="
                f"{self.analytics_productivity_weight_completion}, "
                f"..._deadline={self.analytics_productivity_weight_deadline}, "
                f"..._consistency={self.analytics_productivity_weight_consistency}, "
                f"..._focus={self.analytics_productivity_weight_focus})."
            )
        if any(value < 0 for value in self.analytics_productivity_weights.values()):
            raise ValueError("The analytics productivity weights must not be negative.")
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
