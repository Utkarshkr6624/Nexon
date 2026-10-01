"""Top-level API router.

``main.py`` mounts this router at ``settings.api_v1_prefix`` (``/api/v1``), so
no prefix is declared here. Domain errors raised by the services are turned
into the shared error envelope by the handlers installed from
``app.core.exceptions``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.router import api_v1_router

api_router = APIRouter()
api_router.include_router(api_v1_router)

__all__ = ["api_router"]
