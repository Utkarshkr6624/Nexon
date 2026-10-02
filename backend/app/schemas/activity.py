"""Activity-feed read models.

``activity_events`` is the append-only record of what happened: every
lifecycle transition, every meaningful edit, written by the services rather
than derived on read. It is what lets a client show "who completed this, and
when" without a per-field audit query.

Rows outlive the things they describe. The foreign keys to users, projects and
tasks are ``SET NULL``, not ``CASCADE``, so deleting a project leaves its
history behind with a null ``project_id`` instead of deleting it — losing the
record of what happened in a deleted project is worse than keeping a dangling
one. The feed is therefore a flat list with optional references, and the
``project_id``/``task_id`` filters have to cope with nulls.

The JSONB column is the attribute ``metadata_`` on the model — ``metadata`` is
the declarative class's own column registry — while the wire name is
``metadata``. :class:`ActivityEventRead` carries the rename as a
``validation_alias`` so the two never have to be reconciled by hand at each
call site.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasPath, BaseModel, ConfigDict, Field

from app.schemas.common import PageMeta

__all__ = ["ActivityEventRead", "ActivityFeed"]


class ActivityEventRead(BaseModel):
    """One recorded event.

    ``metadata`` carries non-sensitive context only — the field name it was
    given before it changed, the note attached to a transition. It is rendered
    into exports and kept longer than the sessions it describes, so a token or a
    credential that lands here cannot be un-leaked.

    **``validation_alias`` is load-bearing.** The ORM attribute is ``metadata_``
    while the column is ``metadata``, because the bare name is a declarative
    class's own column registry. Without the alias, ``from_attributes`` would
    read ``row.metadata`` — the table's ``MetaData`` object — and reject every
    row in the feed. The alias points at ``metadata_`` on the way in, the wire
    name stays ``metadata`` on the way out, and ``populate_by_name`` keeps the
    field constructible by name from a dict.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    user_id: UUID | None = Field(
        default=None,
        description="Null when the actor's account was deleted; the event itself is kept.",
    )
    project_id: UUID | None
    task_id: UUID | None
    event_type: str = Field(description="One of the ``ActivityEvent`` values.")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasPath("metadata_"),
        description="Non-sensitive event context; never a credential.",
    )
    created_at: datetime

    @classmethod
    def from_row(cls, row: Any) -> ActivityEventRead:
        """Build the wire model from an ``ActivityLog`` ORM row.

        Equivalent to ``model_validate(row)`` — the alias above already points at
        the renamed attribute — and named for readability at the call sites that
        assemble a feed, so the ORM type is visible in the signature.

        Args:
            row: The persisted event.

        Returns:
            The event with ``metadata`` read from ``row.metadata_``.
        """
        return cls.model_validate(row)


class ActivityFeed(BaseModel):
    """A page of events plus the counters describing the slice.

    The same ``items``/``meta`` shape as :class:`~app.schemas.common.Page`, kept
    as its own type because the feed is also served filtered by project or task
    and the endpoint documents those filters on this body. Reusing
    :class:`~app.schemas.common.PageMeta` is what keeps the counters mean the
    same thing on every list in the API.
    """

    items: list[ActivityEventRead]
    meta: PageMeta
