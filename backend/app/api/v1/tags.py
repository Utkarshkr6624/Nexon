"""Tag endpoints: the caller's own palette, and nothing outside it.

Where the rules live
--------------------
This file is a translation layer and nothing else. Every question it could
answer — *is that name taken, is this tag yours, what is it applied to* — is
answered by :class:`~app.services.tag_service.TagService`, and this router picks
a status code and hands the result back. It imports no repository, and it raises
no domain error: :func:`app.core.exceptions.install_exception_handlers` turns the
service's ``NotFoundError`` / ``ConflictError`` / ``ValidationError`` into the
shared envelope, and a router that built its own error body would be a second,
drifting implementation of the same contract.

Tenancy, in particular, is the service's. **No route here accepts a user id from
the request.** The caller comes from the bearer token, is passed to the service as
``owner=``, and every ``{tag_id}`` is resolved through a lookup scoped by
``user_id`` before it is used. That is what makes another account's tag id answer
**404 and not 403** — identical to an id that never existed — so this surface
cannot be used to find out which tag ids are real. A ``403`` would mean "this id
exists and is not yours", which is the one answer it must never give.

Why one name can be two tags
----------------------------
Uniqueness is ``UNIQUE (user_id, name)``, not ``name``, so two accounts may both
have a tag called ``urgent`` and neither collides. That is deliberate — a shared
namespace would make one user's re-spelling of a label silently restyle another's
board — and it is why a duplicate is only ever a conflict *within one account*,
never across two.

Usage counts
------------
``task_count`` and ``project_count`` are **not** properties of the ``tags`` row:
they are joins through ``task_tags`` and ``project_tags``, and the service fills
them for the whole page in one query. A router that answered ``0`` for both, or
answered the same combined number for both, would render "2 tasks, 2 projects" for
a tag applied to one of each — a lie that looks like data.

Pagination
----------
The listing is a :class:`~app.schemas.common.Page`, never a bare array, and
``limit`` is capped at :data:`MAX_PAGE_SIZE`. The cap is a **rejection, not a
silent truncation**: ``?limit=500`` is a 422 rather than a 500-row page, because
a client that asked for 500 and received 100 cannot tell a truncated page from a
page that was always 100 rows long.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import AuthenticatedUser, TagServiceDep
from app.core.deps import require_permission
from app.core.permissions import Permission
from app.models.tag import Tag
from app.schemas.common import Page
from app.schemas.tag import TagCreate, TagRead

router = APIRouter(prefix="/tags", tags=["tags"])

#: The page size a caller gets when it does not ask for one. Matches
#: :data:`~app.services.tag_service.TagService.DEFAULT_PAGE_SIZE` — a tag list is
#: a palette the user scans for one word, and a person has tens of tags, not
#: thousands.
DEFAULT_PAGE_SIZE = 100

#: The largest page any caller may ask for. 100 is a ceiling, not a default: it
#: is more than any palette in the product renders, so a caller reaching it is a
#: script exporting data rather than a UI, and such a caller is served by walking
#: ``offset``.
MAX_PAGE_SIZE = 100


@router.get(
    "",
    response_model=Page[TagRead],
    summary="List the caller's tags",
    dependencies=[Depends(require_permission(Permission.TASKS_READ))],
)
async def list_tags(
    current_user: AuthenticatedUser,
    tags: TagServiceDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
    search: Annotated[
        str | None,
        Query(description="Case-insensitive substring of the tag name."),
    ] = None,
) -> Page[TagRead]:
    """List the caller's tags alphabetically, each with its usage counts.

    **The scope is the caller's and only the caller's.** There is no ``user_id``
    parameter and no way to ask for somebody else's palette.

    ``task_count`` and ``project_count`` are counted across this user's work only,
    so a tag's reach can never reveal that somebody else applied it.

    ``search`` is a substring match, not a prefix and not a fuzzy one — a filter
    box that matched ``bug`` when the user typed ``debug`` would be guessing for
    them. It is a *rejection* of nothing: an unmatched term is an empty page, not
    an error, because "no tag matches" is a legitimate answer to a filter.

    Errors: 422 for a ``limit`` outside 1-100 or a negative ``offset``.
    """
    return await tags.list(owner=current_user, limit=limit, offset=offset, search=search)


@router.post(
    "",
    response_model=TagRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a tag",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def create_tag(
    payload: TagCreate,
    current_user: AuthenticatedUser,
    tags: TagServiceDep,
) -> Tag:
    """Coin a label for the caller's own work.

    **The owner is the caller and cannot be named.** ``user_id`` comes from the
    bearer token; a create endpoint that let the body choose its owner would let
    any authenticated user write into another account's palette.

    **A name this user already holds is a 409**, not a 500 and not a silent
    reuse of the existing row. Two different accounts may both have "urgent" —
    that is the point of per-user tags — but one account may not have it twice.

    The name is stripped and **not** case-folded: ``Bug`` and ``bug`` stay two
    labels a user may deliberately want, exactly as the uniqueness constraint
    compares them.

    Errors: 409 for a name this user already has; 422 for a blank or over-long
    name.
    """
    return await tags.create(owner=current_user, data=payload)


@router.get(
    "/{tag_id}",
    response_model=TagRead,
    summary="Fetch one tag",
    dependencies=[Depends(require_permission(Permission.TASKS_READ))],
)
async def get_tag(
    tag_id: UUID,
    current_user: AuthenticatedUser,
    tags: TagServiceDep,
) -> Tag:
    """Return one of the caller's tags.

    **404 for another account's tag, never 403** — the id is resolved through a
    lookup scoped by ``user_id``, so the row is never loaded and the refusal is
    the one a nonexistent id gets. See this module's docstring for why that
    distinction is load-bearing.

    The counts on this response are the schema's zero defaults: the service only
    fills them for the listing, where it has the whole page to count in one
    query. A tag used on nothing is correctly ``0``/``0``; a tag used on three
    tasks also answers ``0``/``0`` here. That is a real gap in the response
    contract rather than a rendering choice, and closing it is a ``get_read`` on
    the service — the same shape ``TaskService._page`` already assembles — not a
    router change.

    Errors: 404 when the caller owns no tag with this id.
    """
    return await tags.get(tag_id=tag_id, owner=current_user)


@router.put(
    "/{tag_id}",
    response_model=TagRead,
    summary="Rename a tag",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def rename_tag(
    tag_id: UUID,
    payload: TagCreate,
    current_user: AuthenticatedUser,
    tags: TagServiceDep,
) -> Tag:
    """Give one of the caller's tags a new name.

    **PUT, not PATCH.** The only thing to change about a tag is its name, and
    ``TagCreate`` is exactly the payload that describes one — it is already the
    wire shape for "a tag called X". A ``TagUpdate`` with one optional field would
    need a second model to say the same thing, and the ambiguity a partial update
    invites ("is an omitted name a rename to nothing?") has no useful reading.

    **Renaming to a name this user already holds is a 409**, and renaming to the
    name the tag *already* has is a no-op rather than a conflict: it collides with
    itself, and answering a retried rename with a conflict would make the
    idempotent path the one that fails.

    Errors: 409 for a name this user already has on a different tag; 404 for a
    tag that is not the caller's; 422 for a blank name.
    """
    tag = await tags.get(tag_id=tag_id, owner=current_user)
    return await tags.rename(tag=tag, name=payload.name, owner=current_user)


@router.delete(
    "/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a tag",
    dependencies=[Depends(require_permission(Permission.TASKS_WRITE))],
)
async def delete_tag(
    tag_id: UUID,
    current_user: AuthenticatedUser,
    tags: TagServiceDep,
) -> Response:
    """Delete one of the caller's tags, and every edge pointing at it.

    **The tag edges cascade with it.** A label nobody can name any more is an
    association row that would otherwise sit in every task and project it was ever
    applied to, with nothing to display it from. Deleting a *task* takes its edges
    and leaves the tag alone; this is the other direction.

    Errors: 404 for a tag that is not the caller's, or that does not exist.
    """
    tag = await tags.get(tag_id=tag_id, owner=current_user)
    await tags.delete(tag=tag, owner=current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


#: The router only; the handlers are reached through it, not imported directly.
__all__ = ["router"]
