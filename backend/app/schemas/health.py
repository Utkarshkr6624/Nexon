"""Health-check response models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

DatabaseStatus = Literal["connected", "unavailable"]
OverallStatus = Literal["healthy", "degraded"]


class DatabaseHealth(BaseModel):
    """Result of the database probe."""

    status: DatabaseStatus
    latency_ms: float


class HealthResponse(BaseModel):
    """Detailed health payload served from ``GET /api/v1/health``."""

    status: OverallStatus
    app: str
    version: str
    environment: str
    database: DatabaseHealth
    uptime_seconds: float
    timestamp: str
