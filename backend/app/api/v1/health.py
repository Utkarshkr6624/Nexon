"""Detailed health endpoint.

``GET /api/v1/health`` reports application metadata and probes the database.
A degraded database still yields ``200``: the endpoint reporting that the
service is degraded is itself healthy.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Request

from app.core.config import get_settings
from app.db.session import check_database_connection
from app.schemas.health import DatabaseHealth, HealthResponse

router = APIRouter(tags=["health"])

#: Process start reference used when ``app.state.start_time`` is not provided
#: by the application factory.
_FALLBACK_START = time.monotonic()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _uptime_seconds(request: Request) -> float:
    state_start = getattr(request.app.state, "start_time", None)
    started_monotonic = state_start if isinstance(state_start, float) else _FALLBACK_START
    return round(time.monotonic() - started_monotonic, 2)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Detailed health report",
    description="Reports application metadata plus a timed database connectivity probe.",
)
async def health(request: Request) -> HealthResponse:
    """Report service metadata and a timed database probe."""
    settings = get_settings()

    started = time.perf_counter()
    database_up = await check_database_connection()
    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    return HealthResponse(
        status="healthy" if database_up else "degraded",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        database=DatabaseHealth(
            status="connected" if database_up else "unavailable",
            latency_ms=latency_ms,
        ),
        uptime_seconds=_uptime_seconds(request),
        timestamp=_utc_now_iso(),
    )
