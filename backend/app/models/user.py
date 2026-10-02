"""User account model.

Phase 1 introduced the ``users`` table only, so that the JWT authentication
architecture, the repository/service layers and the migration pipeline were
real and exercised end to end. Phase 2 grows the same table into the full
account shape — a username to sign in with, a display name and avatar for the
product surface, a role for authorisation, and a password-change timestamp so a
password change can invalidate older sessions.

Phase 2 renamed ``full_name`` to ``display_name``. The product surface calls it
a display name everywhere, and keeping a differently-named column would force a
translation at every call site that reads or writes it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = ["DEFAULT_ROLE", "User", "UserRole", "validate_role"]

#: Postgres stores emails case-insensitively for uniqueness.
_MAX_EMAIL_LENGTH = 320
_MAX_DISPLAY_NAME_LENGTH = 255
_MAX_USERNAME_LENGTH = 32
#: Long enough for any provider-hosted image plus its query string.
_MAX_AVATAR_URL_LENGTH = 2048
#: Sized for the longest role name in :class:`UserRole`, with room to spare.
_MAX_ROLE_LENGTH = 16


class UserRole(StrEnum):
    """The roles an account may hold.

    A :class:`~enum.StrEnum` so each member's value is the stable string that is
    actually persisted. ``app.core.permissions`` deliberately duplicates these
    two values as plain strings so that ``app.core`` stays importable without
    pulling in the ORM layer; the two definitions must change together.
    """

    USER = "user"
    ADMIN = "admin"


#: What a new account gets when the caller does not choose.
DEFAULT_ROLE = UserRole.USER.value


def validate_role(value: UserRole | str) -> UserRole:
    """Coerce a stored or user-supplied value into a :class:`UserRole`.

    Raises:
        ValueError: If the value is not a known role. Rejecting an unknown role
            loudly at the boundary keeps a typo from becoming an account that
            silently passes every permission check as an unrecognised — and
            therefore unprivileged — principal.
    """
    if isinstance(value, UserRole):
        return value
    try:
        return UserRole(value)
    except ValueError:
        raise ValueError(f"Unknown role: {value!r}") from None


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An authenticated principal of the platform."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(_MAX_EMAIL_LENGTH),
        unique=True,
        index=True,
        nullable=False,
    )
    # Uniqueness is folded into the index for the same reason as ``email``:
    # ``unique=True, index=True`` makes SQLAlchemy emit one unique index rather
    # than a column plus a separate UniqueConstraint, so the constraint and the
    # lookup path are the same object.
    username: Mapped[str] = mapped_column(
        String(_MAX_USERNAME_LENGTH),
        unique=True,
        index=True,
        nullable=False,
    )
    # Renamed from ``full_name`` in Phase 2; same type, still optional.
    display_name: Mapped[str | None] = mapped_column(
        String(_MAX_DISPLAY_NAME_LENGTH), nullable=True
    )
    avatar_url: Mapped[str | None] = mapped_column(String(_MAX_AVATAR_URL_LENGTH), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # A plain string rather than a native Postgres enum. Adding a value to a
    # Postgres enum needs ``ALTER TYPE ... ADD VALUE``, which cannot run inside
    # a transaction block on some deployment paths (notably older Postgres and
    # inside a wrapped transaction), which is exactly the kind of migration that
    # fails halfway through a deploy. A string with an application-side
    # :class:`UserRole` and :func:`validate_role` adds a role in an ordinary
    # transaction; the trade-off is that the database no longer rejects a
    # misspelled role on its own.
    role: Mapped[str] = mapped_column(
        String(_MAX_ROLE_LENGTH),
        server_default=DEFAULT_ROLE,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Nullable, and deliberately not defaulted to the creation time: rows that
    # predate this column have no value to backfill truthfully, and a fabricated
    # "password set at" timestamp would make every pre-existing session look
    # older than the password and log the owner out on first sign-in. NULL is
    # read as "never changed since signup", which is the correct answer for those
    # rows; the first password change stamps it.
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User id={self.id} email={self.email!r}>"

    @property
    def is_anonymous(self) -> bool:
        """Mirror of FastAPI's ``is_authenticated`` contract, for readability."""
        return False

    @property
    def role_enum(self) -> UserRole:
        """The account's role as a member, for comparisons against :class:`UserRole`.

        Falls back to :attr:`UserRole.USER` for a value this build does not
        recognise, matching the fail-closed behaviour of
        :func:`app.core.permissions.permissions_for`: a drifted role must deny,
        not crash the request that reads it.
        """
        try:
            return UserRole(self.role)
        except ValueError:
            return UserRole(DEFAULT_ROLE)
