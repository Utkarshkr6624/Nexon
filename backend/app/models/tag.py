"""Tags and the two association tables that attach them.

Tags are **per user**. A tag row belongs to exactly one account, and that is the
single most consequential decision in this module: two people working in the same
NEXUS installation do not share a tag namespace, cannot collide on a name, and
cannot see each other's labels. The alternative — a global tag table — makes
"in-progress", "waiting-on" and a personal "someday" the same row, so one user's
recolouring silently restyles another's board and a privacy setting would have
to be bolted on afterwards to undo what the schema already did.

The cost is honest and worth stating: cross-user tag analytics ("which of these
fourteen labels is overloaded across the team") is not expressible, and the UI
cannot offer a shared palette. Phase 3 is a personal, local-first planner, and
the isolation is worth more than the roll-up.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Column, ForeignKey, String, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = ["Tag", "project_tags", "task_tags"]

#: A tag is a short label typed into a filter box, not a sentence.
_MAX_NAME_LENGTH = 48


class Tag(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One label owned by one user."""

    __tablename__ = "tags"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(_MAX_NAME_LENGTH), nullable=False)

    __table_args__ = (
        # Uniqueness is scoped to the owner, which is what makes a tag *belong*
        # to someone rather than merely be spelled a certain way: two users may
        # both have "blocked", and neither can have two. Contrast
        # `users.email`, where `unique=True, index=True` folds the constraint
        # into the index — that works there because the indexed column is itself
        # unique. Here the indexed column is `user_id`, which is emphatically not,
        # so the pair constraint has to be stated on its own.
        UniqueConstraint("user_id", "name", name="uq_tags_user_id_name"),
    )

    # `created_at` is when the label was coined; `updated_at` is when it was last
    # touched. Both come from the mixin and neither is an integrity concern —
    # they exist because a tag is a first-class row that the UI can rename and
    # the API can report on, not a bare join row.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Tag id={self.id} name={self.name!r}>"


# Declarative `Table` objects rather than models: neither association has a
# column of its own to hold state, so an ORM class would add a mapper, a
# constructor and a lazy-load trap for no gain. The repositories join these
# tables explicitly.
#
# Both use a composite primary key on the pair, which is the whole constraint
# that matters: a task cannot carry the same tag twice, and re-applying a tag is
# an idempotent no-op rather than a duplicate that every "show this task's tags"
# query lists twice.
#
# Neither has an index on `tag_id` alone, and that is a deliberate omission. The
# tag filter in the task list is always combined with a task-side term, so the
# plan is the reverse one: walk `ix_tasks_project_id` (or
# `ix_tasks_owner_status_due`) and probe this table by the `(task_id, tag_id)`
# prefix, which the composite primary key already serves. An index on `tag_id`
# would only help a query that filters by tag and nothing else, and would have to
# be maintained for every one of those tag applications.

task_tags = Table(
    "task_tags",
    Base.metadata,
    Column(
        "task_id", UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    ),
    Column(
        "tag_id", UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    ),
)

project_tags = Table(
    "project_tags",
    Base.metadata,
    Column(
        "project_id",
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id", UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    ),
)
