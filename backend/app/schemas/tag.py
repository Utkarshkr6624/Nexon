"""Tag request/response models.

Tags are free-form labels a user applies to projects and tasks. They are
**per-user**: the uniqueness constraint in ``app.models.tag`` is on
``(user_id, name)``, not on ``name`` alone, so two accounts can both have a tag
called "urgent" without colliding, and neither can see or attach the other's.
This module does not repeat that rule; it only makes sure a tag the caller sends
is one they can own.

Counts on :class:`TagRead` are supplied by the service from the two association
tables — a tag's reach across a user's work is the question a tag page asks, and
it cannot be answered from the tag row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "MAX_TAGS_PER_OBJECT",
    "MAX_TAG_NAME_LENGTH",
    "TagAssignment",
    "TagCreate",
    "TagRead",
]

#: Must match ``tags.name`` in ``app.models.tag``.
MAX_TAG_NAME_LENGTH = 48
#: Enough for any realistic labelling of one project or task, and small enough
#: that one request cannot turn into an unbounded join insert.
MAX_TAGS_PER_OBJECT = 50


class TagCreate(BaseModel):
    """Creation payload for a tag.

    The name is stripped but not case-folded: tags are shown back to the person
    who made them, and "Backlog" and "backlog" are labels they may deliberately
    want to be different. Folding the case would silently merge two tags the
    owner can see as two.
    """

    name: str = Field(
        min_length=1,
        max_length=MAX_TAG_NAME_LENGTH,
        examples=["needs-review"],
    )

    @field_validator("name", mode="before")
    @classmethod
    def _strip_name(cls, value: Any) -> Any:
        """Strip surrounding whitespace and reject a name that was only whitespace.

        Without the explicit blank check a name of ``"   "`` would arrive at
        ``min_length`` already looking like a length problem, which is a confusing
        answer for a field the client believes it filled in.
        """
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("Tag name must not be blank.")
            return stripped
        return value


class TagRead(BaseModel):
    """A tag as returned by the tag endpoints.

    ``task_count`` and ``project_count`` default to zero so a tag read without
    the association joins is still answerable — it just says "we did not look",
    which the client cannot distinguish from "none". The list endpoints always
    supply them.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime
    task_count: int = Field(default=0, ge=0)
    project_count: int = Field(default=0, ge=0)


class TagAssignment(BaseModel):
    """The complete set of tags on one project or task.

    A replacement rather than an addition: the client sends the tags the object
    should end up with. An additive endpoint would need a second call to undo a
    mistake, and the mistake here is a tag the client cannot see.
    """

    tag_ids: list[UUID] = Field(
        max_length=MAX_TAGS_PER_OBJECT,
        description="The full set of tags to apply, replacing any existing set.",
        examples=[["0f8f6a52-2b0e-4a1d-9c5b-9f2a6c1d4e77"]],
    )

    @field_validator("tag_ids")
    @classmethod
    def _deduplicate(cls, value: list[UUID]) -> list[UUID]:
        """Drop repeats, preserving order.

        The association tables are keyed on the ``(object, tag)`` pair, so a
        repeated id would reach the database as a duplicate-key violation and
        surface as a 500. The client sent a list, not a multiset, so answering
        with a 422 for something it did correctly spell would be the worse
        failure.
        """
        seen: set[UUID] = set()
        unique: list[UUID] = []
        for tag_id in value:
            if tag_id not in seen:
                seen.add(tag_id)
                unique.append(tag_id)
        return unique
