"""Single-use password reset tokens.

A reset token is a high-entropy signed JWT carrying its own id as ``jti``, not a
user-chosen secret, so only its SHA-256 digest is stored. The row exists to make
a reset strictly single-use: once :attr:`PasswordResetToken.used_at` is set the
link is dead even if it has not expired yet, so a link that leaks into a shared
inbox or a proxy log cannot be replayed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = ["PasswordResetToken"]

#: ``hashlib.sha256(...).hexdigest()`` — always 64 hex characters.
_TOKEN_HASH_LENGTH = 64


class PasswordResetToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One outstanding password reset request.

    Issued tokens are not deleted on use or on expiry; they are left in place so
    the ``users`` foreign key cascade stays the only way a row disappears, and so
    an attempt to reuse a spent token can be distinguished from an attempt to use
    one that never existed.
    """

    __tablename__ = "password_reset_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    # SHA-256 hex of the presented token. The raw token exists only in the reset
    # email, so a database dump must never yield a usable reset token.
    token_hash: Mapped[str] = mapped_column(String(_TOKEN_HASH_LENGTH), index=True, nullable=False)
    # Short window by design: the address is only demonstrably reachable while
    # the requester still has the mailbox.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set the moment the token is redeemed. Together with ``expires_at`` this
    # gives a token exactly one usable moment, so replay inside the window is
    # rejected rather than merely discouraged.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
