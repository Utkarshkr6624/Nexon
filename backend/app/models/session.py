"""Refresh-token sessions.

One row is one browser/device sign-in, not one token. A refresh token is
rotated on every use: the old token's digest is replaced in place by the new
one, and the row is only removed when the user revokes the session or it
expires. That is what makes "sign out everywhere" a single UPDATE rather than
a search through a token table, and it is why :attr:`Session.token_hash` holds
*the current* token rather than a history.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = ["Session"]

#: Maximum textual length of an IPv6 address, plus slack for a port or a
#: proxy-supplied form. Stores the address as text rather than ``inet`` so the
#: value survives a round trip through a JSON API unchanged.
_MAX_IP_LENGTH = 45
_MAX_USER_AGENT_LENGTH = 512
#: ``hashlib.sha256(...).hexdigest()`` — always 64 hex characters.
_TOKEN_HASH_LENGTH = 64


class Session(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single device's live sign-in.

    A refresh token carries this row's id in its ``sid`` claim, which is how the
    API resolves a presented token to the device it belongs to. Revoking a
    session only sets :attr:`revoked_at`; the row is kept so the UI can show
    "this device was signed out on ...".

    No ``User.sessions`` relationship is declared. A plain relationship defaults
    to lazy ``select`` loading, which under AsyncIO raises ``MissingGreenlet``
    the moment a caller touches it outside an awaited query — a surprise worth
    more than the convenience. The repositories query these rows explicitly with
    ``select()`` instead.
    """

    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # SHA-256 hex of the current refresh token. The raw token is never persisted
    # and never logged, so a database dump must never yield a usable refresh
    # token — the digest is the whole of the stored secret.
    token_hash: Mapped[str] = mapped_column(String(_TOKEN_HASH_LENGTH), index=True, nullable=False)
    # The raw User-Agent header, kept verbatim so the device label shown in the
    # sessions UI can be derived the same way every time.
    user_agent: Mapped[str | None] = mapped_column(String(_MAX_USER_AGENT_LENGTH), nullable=True)
    # An audit aid for "where did this sign-in come from", not a security
    # control: it is never used to make an authorisation decision, and a
    # spoofed value costs nothing.
    ip_address: Mapped[str | None] = mapped_column(String(_MAX_IP_LENGTH), nullable=True)
    # When the refresh token stops being usable. Compared against
    # ``func.now()`` server-side, so a revoked-looking row cannot be revived by
    # sending a stale clock.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Stamped on each refresh. Drives the "last active" column in the sessions
    # UI; nullable because a session that has never been refreshed has no such
    # moment yet.
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # NULL means live. This is the single column the repository filters on to
    # list a user's live sessions and the one it updates to revoke them.
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Index note: ``ix_sessions_user_id`` is deliberately the only index on the
    # revoked/lookup path. Both real queries — "list this user's live sessions"
    # and "revoke this user's live sessions" — filter on ``user_id`` first, and
    # ``revoked_at`` is then applied to the handful of rows that index returns.
    # A composite ``(user_id, revoked_at)`` would duplicate a prefix that is
    # already indexed, in a table where a single account holds a few dozen rows
    # at most. An index on ``revoked_at`` alone would be worse still: revoking
    # is always scoped to one user, so almost no query can use it.
