"""User-facing business rules that are not tied to authentication.

Profile reads, profile updates and account deletion. Anything that issues or
retires a credential lives in :mod:`app.services.auth_service` instead, so that
the rules about who may change what are stated in one place.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import ConflictError, NotFoundError, UnauthorizedError
from app.core.security import verify_password
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserUpdate

_USERNAME_CONFLICT = "That username is already taken."
_WRONG_PASSWORD = "The password is incorrect."


class UserService:
    """Reads and updates of user profiles."""

    def __init__(self, repository: UserRepository) -> None:
        self.repository = repository

    async def get_by_id(self, user_id: uuid.UUID) -> User:
        """Return the user or raise :class:`NotFoundError`."""
        user = await self.repository.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User not found.")
        return user

    async def get_active_by_id(self, user_id: uuid.UUID) -> User:
        """Return the user, rejecting deactivated accounts."""
        user = await self.get_by_id(user_id)
        if not user.is_active:
            raise UnauthorizedError("This account is inactive.")
        return user

    async def update(self, user: User, data: UserUpdate) -> User:
        """Apply a partial profile update, guarding username uniqueness.

        The username rule is enforced twice, as registration does it: the
        pre-check rejects the common case, and the commit is guarded as well so
        that losing the race against a concurrent write produces the same 409
        instead of escaping as a driver error. The pre-check is an existence
        check rather than an ownership check — a caller who already holds the
        name writes the value they already have, and the unique index is
        satisfied.

        Email and password are not editable here, by design of
        :class:`~app.schemas.user.UserUpdate`: both are identity or credential
        transitions with rules of their own (verification, session revocation,
        audit rows), and folding them into a profile edit is how one of those
        rules gets forgotten.
        """
        fields: dict[str, object] = {}
        if data.username is not None:
            # Trimmed here rather than left to the schema or to
            # ``update_fields``'s bare setattr: the value stored has to be the
            # value the uniqueness lookup compares against.
            username = data.username.strip()
            if await self.repository.exists_by_username(username):
                raise ConflictError(_USERNAME_CONFLICT)
            fields["username"] = username
        if data.display_name is not None:
            fields["display_name"] = data.display_name
        if data.avatar_url is not None:
            fields["avatar_url"] = data.avatar_url
        if not fields:
            return user
        try:
            return await self.repository.update_fields(user, **fields)
        except IntegrityError as exc:
            # Lost the race against a concurrent update claiming this username.
            raise ConflictError(_USERNAME_CONFLICT) from exc

    async def delete_account(self, *, user: User, password: str) -> None:
        """Delete an account after verifying the caller's password.

        The password is required even though the caller is already
        authenticated. A password change is the account's undo, and so should
        deletion be: a stolen session token must not be enough to destroy
        everything the owner has in the product. Every other device's sessions
        go with the account, because the sessions rows cascade from it.

        Args:
            user: The account to delete.
            password: The password in force now.

        Raises:
            UnauthorizedError: If the password does not verify.
        """
        if not verify_password(password, user.hashed_password):
            raise UnauthorizedError(_WRONG_PASSWORD)
        await self.repository.delete(user)
