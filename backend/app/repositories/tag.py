"""Data access for :class:`~app.models.tag.Tag` and the two association tables.

The repository owns SQL only. It never raises domain errors — an unexpected
``IntegrityError`` propagates so the service layer can translate it into the API
contract.

Tags are **per user**. The ``tags`` table carries its own ``user_id`` and the
unique constraint is ``(user_id, name)``, so the same word is two rows for two
accounts and a tag someone else's list happens to contain is not visible to them
at all. A shared tag table would have made that word a global namespace: two
users who both want "urgent" would collide, and one user's tag count would leak
into another's sidebar. Per-user tags also mean a tag list needs no ownership
join to be safe — the ``user_id`` predicate *is* the check.

Three things here exist purely to keep the listing queries from becoming one per
row. :meth:`TagRepository.list_tags_for_tasks` returns the tags for a whole page
in a single set-based query, :meth:`TagRepository.list_tags_for_projects` does
the same for project rows, and :meth:`TagRepository.count_usage_by_kind` returns
every tag's task and project counts in one query. A loop over a page of fifty
tasks is fifty round trips to render one screen, and it is the kind of cost that
is invisible in development and obvious in production.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, insert, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.tag import Tag, project_tags, task_tags
from app.models.task import Task

__all__ = ["TagRepository"]


def _search_pattern(term: str) -> str:
    """Wrap a user-supplied search term in a case-insensitive ``ILIKE`` pattern.

    ``ILIKE`` and not ``pg_trgm``: this build of PostgreSQL has no contrib
    modules, so ``pg_trgm`` cannot be created and the trigram indexes that would
    accelerate substring search are unavailable. Depending on an extension the
    target cannot install would make the migration unappliable, so the portable
    operator is the right choice for a per-user list of tens of rows.

    The wildcards inside the term are escaped too — otherwise a search for ``50%``
    matches everything and a search for ``_`` matches any single character.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class TagRepository:
    """Tag persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, *, user_id: uuid.UUID, name: str) -> Tag:
        """Insert a new tag for this user.

        The name is stripped but its case is preserved, because the unique
        constraint ``(user_id, name)`` is case-sensitive and folding case here
        would be a second, invisible normalisation that :meth:`get_by_name` — and
        the collision check above it — does not apply. A user who writes ``Bug``
        gets ``Bug`` back; deduplicating ``bug`` against it is a decision for
        whoever designs the tag input, made once, in one place.

        Raises:
            IntegrityError: If this user already has a tag with that exact name.
                Left to propagate so the service can answer "already exists"
                rather than the repository guessing at the intent.
        """
        tag = Tag(user_id=user_id, name=name.strip())
        self.session.add(tag)
        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    async def get_by_id_for_user(self, tag_id: uuid.UUID, user_id: uuid.UUID) -> Tag | None:
        """Return the tag only when it also belongs to this user.

        Ownership is part of the lookup rather than a check afterwards, so
        another user's tag id returns ``None`` — identically to an id that does
        not exist — and the row is never loaded in the first place.
        """
        result = await self.session.execute(
            select(Tag).where(Tag.id == tag_id, Tag.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, user_id: uuid.UUID, name: str) -> Tag | None:
        """Return this user's tag with this exact name, or ``None``.

        Exact after stripping, matching the case sensitivity of the constraint it
        is standing in for. This is the check that turns a duplicate tag into a
        409 before the ``IntegrityError`` does.
        """
        result = await self.session.execute(
            select(Tag).where(Tag.user_id == user_id, Tag.name == name.strip())
        )
        return result.scalar_one_or_none()

    async def update_name(self, tag: Tag, name: str) -> Tag:
        """Write a new name onto an already-loaded tag.

        The strip mirrors :meth:`create`, so the value stored is the value
        :meth:`get_by_name` and the service's collision check compare. Renaming
        through the same path creation takes means there is one normalisation,
        not one per writer.

        Raises:
            IntegrityError: If this user already has another tag with that exact
                name. Left to propagate for the reason given on :meth:`create`.
        """
        tag.name = name.strip()
        self.session.add(tag)
        await self.session.commit()
        await self.session.refresh(tag)
        return tag

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
        search: str | None = None,
    ) -> tuple[list[Tag], int]:
        """List this user's tags alphabetically, with the unpaginated total.

        Alphabetical by name rather than newest first: a tag list is a lookup
        control the user scans, not a feed they read in order, and ``name`` is the
        only ordering in which scanning finds anything. ``id`` breaks ties so the
        order is total and a page boundary cannot shuffle between calls.
        """
        filters = [Tag.user_id == user_id]
        if search is not None and search.strip():
            filters.append(Tag.name.ilike(_search_pattern(search.strip()), escape="\\"))

        page = (
            select(Tag)
            .where(*filters)
            .order_by(Tag.name.asc(), Tag.id.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(page)
        rows = list(result.scalars().all())

        total = int(
            await self.session.scalar(select(func.count()).select_from(Tag).where(*filters))
        )
        return rows, total

    async def set_task_tags(self, task_id: uuid.UUID, tag_ids: Sequence[uuid.UUID]) -> None:
        """Replace a task's tags with exactly this set.

        Delete-then-insert rather than a diff of the current membership: the
        caller has already decided the full desired set, and reconciling it here
        would need a read first, so the diff would cost three round trips and
        still be wrong under two concurrent edits. Replace-the-whole-set is one
        transaction that either lands or does not.

        The ids are deduplicated because the composite primary key would reject
        the same tag twice, and a caller assembling the list from two filter
        chips can legitimately produce a duplicate.

        Ownership of the tags themselves is the caller's to establish — it has
        already resolved each id through :meth:`get_by_id_for_user` — which is why
        this method takes no ``user_id``. An empty sequence clears the task's
        tags, which is the correct meaning of "set these tags" and not a no-op.
        """
        await self.session.execute(delete(task_tags).where(task_tags.c.task_id == task_id))
        rows = [{"task_id": task_id, "tag_id": tag_id} for tag_id in dict.fromkeys(tag_ids)]
        if rows:
            await self.session.execute(insert(task_tags), rows)
        await self.session.commit()

    async def set_project_tags(self, project_id: uuid.UUID, tag_ids: Sequence[uuid.UUID]) -> None:
        """Replace a project's tags with exactly this set. See :meth:`set_task_tags`."""
        await self.session.execute(
            delete(project_tags).where(project_tags.c.project_id == project_id)
        )
        rows = [{"project_id": project_id, "tag_id": tag_id} for tag_id in dict.fromkeys(tag_ids)]
        if rows:
            await self.session.execute(insert(project_tags), rows)
        await self.session.commit()

    async def list_tags_for_tasks(
        self, task_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[Tag]]:
        """Return the tags of many tasks in one query, keyed by task id.

        The whole reason this method exists. Rendering a page of fifty tasks with
        their tags would otherwise be fifty queries — an N+1 — and the fix is not
        a loop of :meth:`get_by_id_for_user`, it is a single ``IN`` over the ids
        the page already holds.

        Only tasks that *have* tags appear as keys. That is what the join produces
        and what the caller should expect: read with ``result.get(task_id, [])``.
        Pre-seeding every requested id with an empty list would be marginally more
        convenient, and it would also mean constructing one list per row of a
        fifty-row page to hold the 90% of tasks that have no tags at all.

        An empty sequence returns ``{}`` without querying: an ``IN ()`` is either
        a syntax error or a pointless round trip, and this is the common case for
        the last page of a filtered listing.
        """
        if not task_ids:
            return {}
        result = await self.session.execute(
            select(task_tags.c.task_id, Tag)
            .join(Tag, Tag.id == task_tags.c.tag_id)
            .where(task_tags.c.task_id.in_(list(task_ids)))
            .order_by(task_tags.c.task_id.asc(), Tag.name.asc(), Tag.id.asc())
        )
        grouped: dict[uuid.UUID, list[Tag]] = {}
        for task_id, tag in result.all():
            grouped.setdefault(task_id, []).append(tag)
        return grouped

    async def list_tags_for_projects(
        self, project_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[Tag]]:
        """Return the tags of many projects in one query, keyed by project id.

        See :meth:`list_tags_for_tasks` — same query shape, same reason.
        """
        if not project_ids:
            return {}
        result = await self.session.execute(
            select(project_tags.c.project_id, Tag)
            .join(Tag, Tag.id == project_tags.c.tag_id)
            .where(project_tags.c.project_id.in_(list(project_ids)))
            .order_by(project_tags.c.project_id.asc(), Tag.name.asc(), Tag.id.asc())
        )
        grouped: dict[uuid.UUID, list[Tag]] = {}
        for project_id, tag in result.all():
            grouped.setdefault(project_id, []).append(tag)
        return grouped

    async def delete(self, tag: Tag) -> None:
        """Delete a tag row.

        The ``task_tags`` and ``project_tags`` rows cascade with it, which is the
        point: a tag nobody can name any more is a row that would otherwise sit in
        every tagged task forever with nothing to display it from. The reverse is
        not true — deleting a *task* takes its tag edges but never the tag.
        """
        await self.session.delete(tag)
        await self.session.commit()

    async def count_usage_by_kind(self, user_id: uuid.UUID) -> dict[uuid.UUID, tuple[int, int]]:
        """Return ``(task_count, project_count)`` for each of this user's tags.

        One query for both halves, built as ``UNION ALL`` over two ``GROUP BY``
        sides and summed per tag *and per kind* in the outer query. The obvious
        alternatives — a loop of counts, or two queries joined in Python — cost a
        round trip per tag, and the whole point of a "12 tasks, 3 projects"
        affordance is that it renders before the user clicks anything.

        **The two counts are kept apart, and that is the reason this replaced a
        combined total.** :class:`~app.schemas.tag.TagRead` has ``task_count`` and
        ``project_count`` as separate fields, and a single summed number can fill
        neither of them: assigning the sum to both would show a tag applied to one
        task and one project as "2 tasks, 2 projects", and the caller has no way to
        tell that happened. Each side therefore contributes to its own column —
        a ``0`` literal on the opposite side of each branch — so a tag used only on
        tasks reports ``(n, 0)`` rather than a single ``n``.

        A tag used on nothing is absent from the result rather than mapped to
        ``(0, 0)``: the caller joins this against its own tag list, and a zero
        entry would be a row it has to filter out. Rows the user cannot see are
        excluded by scoping each side to that user's rows, so a tag's count can
        never reveal that somebody else used it.
        """
        task_side = (
            select(
                task_tags.c.tag_id.label("tag_id"),
                func.count().label("task_uses"),
                literal(0).label("project_uses"),
            )
            .select_from(task_tags)
            .join(Task, Task.id == task_tags.c.task_id)
            .where(Task.owner_id == user_id)
            .group_by(task_tags.c.tag_id)
        )
        project_side = (
            select(
                project_tags.c.tag_id.label("tag_id"),
                literal(0).label("task_uses"),
                func.count().label("project_uses"),
            )
            .select_from(project_tags)
            .join(Project, Project.id == project_tags.c.project_id)
            .where(Project.owner_id == user_id)
            .group_by(project_tags.c.tag_id)
        )
        combined = task_side.union_all(project_side).subquery()
        result = await self.session.execute(
            select(
                combined.c.tag_id,
                func.coalesce(func.sum(combined.c.task_uses), 0),
                func.coalesce(func.sum(combined.c.project_uses), 0),
            ).group_by(combined.c.tag_id)
        )
        return {tag_id: (int(tasks), int(projects)) for tag_id, tasks, projects in result.all()}
