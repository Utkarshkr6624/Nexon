"""Session list wire models.

A session row is a device sign-in. Only what a user needs to recognise one and
decide whether to revoke it is exposed; see :class:`SessionRead` for what is
deliberately missing.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.session import Session

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

    @classmethod
    def for_request(cls, row: Session, *, current_session_id: UUID | None) -> SessionRead:
        """Build one row's wire model, marking it when the caller is signed in on it.

        **``is_current`` is a property of the request, not of the row.** The same
        row is current in one caller's response and merely historical in
        another's, so persisting the flag would mean a column that is only ever
        true relative to somebody and has to be rewritten per reader. It is
        derived here instead, from the id of the session the request was made
        with, which the caller already holds — so marking costs no query the
        listing did not already make.

        Args:
            row: The persisted session. Read through ``from_attributes``, so the
                column list is not restated here and cannot drift.
            current_session_id: The id from the presented token's ``sid`` claim,
                or ``None`` for a token that carried none. ``None`` marks nothing,
                which is the honest answer for a caller we cannot locate.

        Returns:
            The row as :class:`SessionRead`, with ``is_current`` resolved.
        """
        # ``model_validate`` reads the row; ``model_copy`` is the v2 API that
        # overlays a computed value on an already-validated model. There is no
        # ``update`` argument to ``model_validate`` — passing one is a TypeError
        # the first time this endpoint is called.
        return cls.model_validate(row).model_copy(
            update={"is_current": row.id == current_session_id}
        )


class SessionListRead(BaseModel):
    """The caller's sessions plus the id of the one they are calling from.

    ``current_id`` is stated once at the top level rather than being left to
    the client to infer from the ``is_current`` flag, so the client can compare
    a selected device without a second pass over the list.
    """

    sessions: list[SessionRead]
    current_id: UUID | None = None
