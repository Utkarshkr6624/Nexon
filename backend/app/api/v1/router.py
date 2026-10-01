"""Version 1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, health, users

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(users.router)

__all__ = ["api_v1_router"]
