"""Version 1 of the HTTP API, mounted under ``/api/v1``."""

from app.api.v1.router import api_v1_router

__all__ = ["api_v1_router"]
