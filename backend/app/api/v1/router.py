"""Version 1 router aggregation."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import activity, auth, health, projects, tags, tasks, users

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(users.router)
api_v1_router.include_router(projects.router)
api_v1_router.include_router(tasks.router)
api_v1_router.include_router(tags.router)
api_v1_router.include_router(activity.router)

__all__ = ["api_v1_router"]
