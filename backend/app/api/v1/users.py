"""User profile and account endpoints.

Every route here acts on the *caller*. There is no ``/users/{id}`` on purpose:
ownership is therefore implicit rather than checked, because the only subject a
request can name is the one its bearer token already resolved to.

The one exception is the administrative listing at the bottom, which exists to
give the permission system a gate that is real and observable rather than
merely declared. See its docstring.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import AuthenticatedUser, UserServiceDep
from app.core.deps import get_current_active_superuser, require_permission
from app.core.permissions import Permission
from app.models.user import User
from app.schemas.user import UserDeletion, UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.patch(
    "/me",
    response_model=UserRead,
    summary="Update the caller's profile",
    dependencies=[Depends(require_permission(Permission.USERS_WRITE))],
)
async def update_me(
    payload: UserUpdate,
    current_user: AuthenticatedUser,
    users: UserServiceDep,
) -> User:
    """Update the caller's own profile.

    **Ownership is implicit here**: the route has no path parameter, so the only
    account it can touch is the one the bearer token resolved to. The
    ``USERS_WRITE`` permission is still checked, because it is the grant that
    says "accounts may edit their own profile" at all — the check answers
    *whether the capability exists*, the token answers *whose*. Removing the
    dependency would not make the route safer, only unguarded: it would mean a
    future route added next to this one had no obvious place to hang the
    permission check.

    Email and password are not editable through this route; both have their own
    endpoints because both are identity or credential transitions with rules
    (verification, session revocation, audit rows) that a profile edit has no
    business performing.

    Errors: 409 when the requested username is taken.
    """
    return await users.update(current_user, payload)


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete the caller's account",
)
async def delete_me(
    payload: UserDeletion,
    current_user: AuthenticatedUser,
    users: UserServiceDep,
) -> Response:
    """Delete the caller's account permanently.

    The password is required in addition to the bearer token, and ``confirm``
    must be ``true`` in the payload. A token left in a shared browser is enough
    to *read* an account; it is not enough to destroy one.

    Sessions and outstanding password-reset tokens go with the account by
    database cascade, so every device is signed out without a second pass. The
    audit rows do **not**: ``audit_logs.user_id`` is ``ON DELETE SET NULL``, so an
    account's security history outlives the account and is still readable
    afterwards — a trail that vanished along with the thing it describes could
    not be used to investigate that deletion at all. The service records the
    ``ACCOUNT_DELETED`` row before the row goes, so the surviving record still
    names who it was.

    Errors: 401 when the password does not verify.
    """
    await users.delete_account(user=current_user, password=payload.password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/",
    response_model=list[UserRead],
    summary="List accounts (admin only)",
    dependencies=[
        Depends(require_permission(Permission.USERS_READ)),
        Depends(get_current_active_superuser),
    ],
)
async def list_users(users: UserServiceDep) -> list[User]:
    """List every account — a permission-system fixture, not a product feature.

    It exists so the role → permission wiring has a route whose refusal is
    observable end to end: an ordinary caller must get 403, an administrator must
    get 200, and the difference between them must be the permission check rather
    than an accident of how the route was written. Without a route like this,
    ``ROLE_PERMISSIONS`` is a map nothing ever reads.

    Two gates, deliberately distinct. ``USERS_READ`` is the capability check and
    is what the permission map expresses; requiring the administrator role *as
    well* is the narrowing that says this listing is not part of the product's
    surface. Both must pass. It returns an unbounded list with no pagination
    because a fixture that could itself need pagination would be a worse
    fixture.

    Errors: 403 for any caller whose role does not satisfy both checks.
    """
    return await users.list_all()


__all__ = ["router"]
