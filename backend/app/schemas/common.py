"""Shared response envelopes.

The plain ``Message`` used by endpoints that only acknowledge an action, and
the pagination primitives every later phase reuses. The error envelope lives in
``app.core.exceptions`` because it is produced by exception handlers rather than
by response models.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Message(BaseModel):
    """Acknowledgement body for endpoints with nothing else to return."""

    message: str


class PageMeta(BaseModel):
    """Counters describing the slice returned in a :class:`Page`."""

    total: int = Field(ge=0, description="Total rows matching the query.")
    limit: int = Field(ge=1, description="Maximum number of rows requested.")
    offset: int = Field(ge=0, description="Number of rows skipped.")


class Page[T](BaseModel):
    """Paginated collection envelope."""

    items: list[T]
    meta: PageMeta
