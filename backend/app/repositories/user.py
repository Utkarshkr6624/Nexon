"""Data access for :class:`~app.models.user.User`.

The repository owns SQL only. It never raises domain errors — an unexpected
``IntegrityError`` is allowed to propagate so the service layer can translate
it into the API contract.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import DEFAULT_ROLE, User


class UserRepository:
    """User persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Return the user with this id, or ``None``."""
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Return the user with this email, or ``None``."""
        result = await self.session.execute(select(User).where(User.email == email.strip().lower()))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        """Return the user with this username, or ``None``.

        Stripped but not lower-cased: usernames are shown back to the user, so
        whatever casing they registered with is the casing they sign in with.
        """
        result = await self.session.execute(select(User).where(User.username == username.strip()))
        return result.scalar_one_or_none()

    async def exists_by_email(self, email: str) -> bool:
        """Report whether an account already uses this email."""
        result = await self.session.execute(
            select(func.count()).select_from(User).where(User.email == email.strip().lower())
        )
        return bool(result.scalar_one())

    async def exists_by_username(self, username: str) -> bool:
        """Report whether an account already uses this username."""
        result = await self.session.execute(
            select(func.count()).select_from(User).where(User.username == username.strip())
        )
        return bool(result.scalar_one())

    async def create(
        self,
        *,
        email: str,
        hashed_password: str,
        username: str,
        display_name: str | None = None,
        role: str = DEFAULT_ROLE,
    ) -> User:
        """Insert a new account and return it with server defaults populated.

        ``display_name`` replaced ``full_name`` in Phase 2; it is still optional
        and still purely presentational, so a sign-up is not blocked on it.
        """
        user = User(
            email=email.strip().lower(),
            hashed_password=hashed_password,
            username=username.strip(),
            display_name=display_name,
            role=role,
        )
        self.session.add(user)
        await self.session.commit()
        # created_at/updated_at come from server defaults, so re-read them to
        # guarantee a fully populated instance for the response body.
        await self.session.refresh(user)
        return user

    async def update_fields(self, user: User, **fields: object) -> User:
        """Apply a partial update and persist it.

        ``email`` and ``username`` are normalised again here even though the
        caller has usually normalised them already. These methods are the one
        door into the unique columns, and a value that reached the column
        unnormalised would be invisible to :meth:`get_by_email` and
        :meth:`exists_by_email` — an account that exists but cannot be found,
        or a duplicate slipping past the check that was supposed to prevent it.
        Normalising at the boundary means that failure mode is unreachable
        rather than merely discouraged.
        """
        normalised = dict(fields)
        email = normalised.get("email")
        if isinstance(email, str):
            normalised["email"] = email.strip().lower()
        username = normalised.get("username")
        if isinstance(username, str):
            normalised["username"] = username.strip()
        for key, value in normalised.items():
            setattr(user, key, value)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def delete(self, user: User) -> None:
        """Hard-delete the account row.

        A hard delete, not a soft one: a local-first product holds the user's own
        data, so "delete my account" that quietly leaves the row behind would be
        a false promise. The consequences are handled by the database — the
        ``sessions`` and ``password_reset_tokens`` foreign keys cascade, while
        ``audit_logs.user_id`` is set to NULL by the ``ON DELETE SET NULL``
        rule, because an account's audit trail has to outlive the account.
        """
        await self.session.delete(user)
        await self.session.commit()
