"""Data access for :class:`~app.models.user.User`.

The repository owns SQL only. It never raises domain errors — an unexpected
``IntegrityError`` is allowed to propagate so the service layer can translate
it into the API contract. The one exception is the field allowlist in
:meth:`UserRepository.update_fields`, which rejects a disallowed column name with
:class:`ValueError`: that is a mistake in the calling code, not an outcome of a
request, so it is raised here where the mistake is and never reaches the API.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import DEFAULT_ROLE, User

#: The columns :meth:`UserRepository.update_fields` will write, and the only
#: reason that method has a signature at all. Every one of these is either
#: presentational or part of a transition that has already been authorised and
#: audited upstream of this layer; everything else on ``users`` is structural
#: state that no service may set by accident.
#:
#: ``id``, ``created_at`` and ``updated_at`` are the row's identity and its
#: timeline: writing them would rewrite history or collide with the server
#: defaults. ``role`` and ``is_active`` are authorisation, and a column that any
#: partial update can reach is a column the permission system cannot rely on.
#: ``is_superuser`` is the pre-Phase-2 flag superseded by ``role``; it survives
#: as a column only.
#:
#: Extending this list is a deliberate act with a reviewable diff, which is the
#: whole point. A field that is absent raises :class:`ValueError` rather than
#: being silently dropped, so the caller who expected it to be written finds
#: out at the call site instead of discovering a field that never saved.
_UPDATABLE_FIELDS = frozenset(
    {
        "avatar_url",
        "display_name",
        "email",
        "hashed_password",
        "is_verified",
        "last_login_at",
        "password_changed_at",
        "username",
    }
)


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

    async def exists_by_username(
        self, username: str, *, exclude_user_id: uuid.UUID | None = None
    ) -> bool:
        """Report whether an account already uses this username.

        Args:
            username: The handle to look for. Stripped, not folded, to match
                :meth:`get_by_username`.
            exclude_user_id: A row to leave out of the check. Excluding is what
                makes the question answerable by a *caller updating their own
                profile*: the row that holds the name is their own, so the answer
                they need is "does somebody **else** hold it". Omit it when
                checking a value about to be created, where there is no self-row
                to discount — that is the registration case.

        Returns:
            Whether a row other than ``exclude_user_id`` already holds the name.
        """
        stmt = select(func.count()).select_from(User).where(User.username == username.strip())
        if exclude_user_id is not None:
            stmt = stmt.where(User.id != exclude_user_id)
        result = await self.session.execute(stmt)
        return bool(result.scalar_one())

    async def list_all(self) -> list[User]:
        """Return every account, oldest first.

        Unbounded and unpaginated, which is stated rather than hidden: the one
        caller is the administrative listing that exists to give the permission
        system a real gate, and a fixture that could itself need pagination
        would be a worse fixture. A product surface listing accounts does not
        exist yet, and when one does it needs a keyset, not this method.

        The id is a tiebreak on ``created_at``: two accounts created inside the
        same clock tick would otherwise come back in an order the database chose,
        and a listing whose order varies between calls cannot be diffed.
        """
        result = await self.session.execute(
            select(User).order_by(User.created_at.asc(), User.id.asc())
        )
        return list(result.scalars().all())

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

        Args:
            user: The row to update.
            **fields: The columns to write. Only the names in
                :data:`_UPDATABLE_FIELDS` are accepted; the method used to
                ``setattr`` whatever it was handed, which is safe exactly as
                long as every caller assembles its kwargs by hand and becomes a
                mass-assignment hole the moment one of them forwards a payload —
                ``**payload.model_dump()`` would then be able to write ``role``,
                ``is_active`` or ``created_at``. The allowlist moves that
                judgement from "does every current caller remember" to a single
                reviewable line. Absent keys are left untouched, so ``None`` is
                a value to write (clear the column) and not a way to skip one;
                callers wanting to skip a field leave its key out.

        Returns:
            The updated, refreshed row.

        Raises:
            ValueError: If a field is not on the allowlist. This is a
                programming error rather than user input: the name comes from
                code, not from a request body, and failing loudly at the call
                site beats writing a column nobody meant to write.
        """
        rejected = sorted(set(fields) - _UPDATABLE_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a user row; "
                f"update_fields accepts only {', '.join(sorted(_UPDATABLE_FIELDS))}."
            )
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
