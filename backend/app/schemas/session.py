"""Session list wire models.

A session row is a device sign-in. Only what a user needs to recognise one and
decide whether to revoke it is exposed; see :class:`SessionRead` for what is
deliberately missing.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["SessionListRead", "SessionRead"]


class SessionRead(BaseModel):
    """One device's sign-in, as shown in the sessions UI.

    ``token_hash`` is absent because a digest of a live refresh token is still a
    credential an attacker could compare against, and there is no UI decision
    that needs it. ``user_id`` is absent because every session in the response
    already belongs to the caller: repeating the owner on every row tells the
    client nothing it does not already know, and it is the kind of field that
    ends up rendered in a "revoke" confirmation.

    ``is_current`` is not a column. It is set by the service comparing this
    row's id against the ``sid`` claim of the presented token, because "which of
    these am I?" is a property of the request, not of the row.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    revoked_at: datetime | None
    is_current: bool = Field(
        default=False,
        description="True for the session the request was made with.",
    )


class SessionListRead(BaseModel):
    """The caller's sessions plus the id of the one they are calling from.

    ``current_id`` is stated once at the top level rather than being left to
    the client to infer from the ``is_current`` flag, so the client can compare
    a selected device without a second pass over the list.
    """

    sessions: list[SessionRead]
    current_id: UUID | None = None
