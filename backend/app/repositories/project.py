"""Data access for :class:`~app.models.project.Project`.

The repository owns SQL only. It never raises domain errors — an unexpected
``IntegrityError`` is allowed to propagate so the service layer can translate it
into the API contract. The two exceptions are the guards against a *programming*
error rather than an outcome of a request: the field allowlist in
:meth:`ProjectRepository.update_fields` and the sort allowlist in
:meth:`ProjectRepository.list_for_user`. Both reject a name with
:class:`ValueError`, because the name arrives from code rather than from a
request body and the caller who got it wrong finds out at the call site.

Three ideas run through the file.

**Ownership is a predicate, not a filter.** Every method that takes an
``owner_id`` puts it in the ``WHERE`` clause. Fetching a row and comparing
``row.owner_id`` afterwards in Python would produce the same answer for a
correct caller and a wrong one — a single line of refactoring between "returns
``None``" and "returns another user's project", i.e. an IDOR. Keeping the check
in the query means a row the caller may not see is never loaded at all. That is
what :meth:`ProjectRepository.get_by_id_for_user` demonstrates: knowing an id is
not authorisation, and the id alone buys nothing.

**Filters AND together.** Every filter narrows the same statement, so ``status``
plus ``search`` plus a date window are three restrictions on one row rather than
three independent ones a caller has to intersect. The consequence is that the
``total`` a page reports is the size of the same set the page was drawn from,
which is the only definition of "page 2 of 47" that means anything.

**Counts are asked of the database.** ``total`` comes from a ``COUNT`` over the
filtered query and :meth:`ProjectRepository.stats_for_user` gets every bucket
out of a single ``GROUP BY``. Counting a page is how a paginated UI ends up
showing "1-50 of 50" on page four.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.enums import ProjectStatus
from app.models.project import Project

__all__ = ["ProjectRepository"]

#: The columns :meth:`ProjectRepository.update_fields` will write, and the only
#: reason that method has a signature at all.
#:
#: ``id``, ``created_at`` and ``updated_at`` are the row's identity and its
#: timeline: writing them would rewrite history or collide with the server
#: defaults. ``owner_id`` is the authorisation anchor — a partial update that can
#: reach it is a partial update that can hand a project to another account.
#: Everything else is state a service has already decided to change.
#:
#: As in :data:`app.repositories.user._UPDATABLE_FIELDS`, extending this list is
#: a deliberate act with a reviewable diff, and an absent field raises rather
#: than being dropped, so a caller who expected a column to be written finds out
#: here instead of discovering a field that never saved.
_UPDATABLE_FIELDS = frozenset(
    {
        "archived_at",
        "completed_at",
        "description",
        "name",
        "priority",
        "start_date",
        "status",
        "target_date",
    }
)

#: Public sort names mapped to column attributes.
#:
#: ``ORDER BY`` cannot take a bound parameter — the grammar wants an expression,
#: not a value — so a sort name arriving from a request that was interpolated
#: into it would be SQL injection with a working ``sort`` query parameter. The
#: alternative, which this file uses, is to resolve the name against this table
#: and fail closed: an unknown name is a :class:`ValueError`, never a fallback to
#: some default column, because a silent fallback would return a *plausible*
#: ordering for a request that asked for a different one.
_SORT_COLUMNS: dict[str, InstrumentedAttribute] = {
    "created_at": Project.created_at,
    "start_date": Project.start_date,
    "status": Project.status,
    "target_date": Project.target_date,
    "updated_at": Project.updated_at,
    "name": Project.name,
    "priority": Project.priority,
}


def _search_pattern(term: str) -> str:
    """Wrap a user-supplied search term in a case-insensitive ``ILIKE`` pattern.

    ``ILIKE`` and not ``pg_trgm``: this build of PostgreSQL has no contrib
    modules, so ``CREATE EXTENSION pg_trgm`` fails with "extension is not
    available" and the trigram indexes that would make substring search fast
    cannot be created at all. Depending on an extension the target cannot install
    would make the migration unappliable, so the portable operator is the right
    choice here; the cost is that a leading ``%`` cannot use a B-tree index, and
    that is the honest trade for a work-management listing that is per-user and
    therefore small.

    The wildcards inside the term are escaped as well. Without this, a search for
    ``50%`` matches every row and a search for ``_`` matches any single
    character — a caller-supplied string silently changing what the query means.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _order_by_clauses(sort: str, order: str) -> tuple[InstrumentedAttribute, ...]:
    """Resolve a public ``sort``/``order`` pair into ORDER BY expressions.

    Args:
        sort: A key of :data:`_SORT_COLUMNS`.
        order: ``"asc"`` or ``"desc"``.

    Returns:
        The chosen column, and the primary key as a tiebreak. The tiebreak is not
        decoration: ``created_at`` is a one-second ``server_default``, so two
        projects created in the same tick can come back in either order, and a
        listing whose order varies between calls cannot be paginated or diffed.

    Raises:
        ValueError: If either name is not on its allowlist. Both are programming
            errors — a route hands the repository a value it has already checked
            against its own enum, and anything else means the mapping between
            those layers has drifted.
    """
    column = _SORT_COLUMNS.get(sort)
    if column is None:
        raise ValueError(
            f"Cannot sort projects by {sort!r}; "
            f"list_for_user accepts only {', '.join(sorted(_SORT_COLUMNS))}."
        )
    if order == "asc":
        return column.asc(), Project.id.asc()
    if order == "desc":
        return column.desc(), Project.id.desc()
    raise ValueError(f"Cannot sort projects {order!r}; expected 'asc' or 'desc'.")


class ProjectRepository:
    """Project persistence bound to a single request-scoped session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        owner_id: uuid.UUID,
        name: str,
        description: str | None = None,
        priority: str = "medium",
    ) -> Project:
        """Insert a new project and return it with server defaults populated.

        ``status`` is left to the column's ``server_default`` rather than being
        passed in. A caller that could choose the status at creation could create
        a project that is already ``completed``, and ``completed_at`` would then
        be NULL — a row that claims to be finished and cannot say when. Status is
        a transition, and a transition needs an event behind it.
        """
        project = Project(
            owner_id=owner_id,
            name=name.strip(),
            description=description,
            priority=priority,
        )
        self.session.add(project)
        await self.session.commit()
        # created_at/updated_at/status come from server defaults, so re-read them
        # rather than handing back an instance whose timestamps are still unset.
        await self.session.refresh(project)
        return project

    async def get_by_id_for_user(
        self, project_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Project | None:
        """Return the project only when it also belongs to this user.

        Ownership is part of the lookup rather than a check afterwards: a caller
        that resolved "project X" must not be able to read or act on it just
        because it knows the id. Presenting another user's id returns ``None``,
        identically to presenting an id that does not exist — the caller cannot
        tell the two apart, which is what keeps a project-existence oracle from
        becoming a project-enumeration one.
        """
        result = await self.session.execute(
            select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        search: str | None = None,
        sort: str = "created_at",
        order: str = "desc",
    ) -> tuple[list[Project], int]:
        """List one owner's projects, newest by default, with the unpaginated total.

        ``search`` is a case-insensitive substring over ``name`` *or*
        ``description``, because a user looking for "invoice" has no reason to
        know whether they typed it into the title or the body. An empty or
        whitespace-only term is treated as no filter rather than as a search for
        ``"%%"``, which would match every row including the NULL ones.

        Returns:
            The page of rows and the total number of rows the filters match —
            counted in SQL, not as ``len(rows)``, so page four of a result set
            that does not exist is an empty page rather than a lie.
        """
        filters = [Project.owner_id == owner_id]
        if status is not None:
            filters.append(Project.status == status)
        if search is not None and search.strip():
            pattern = _search_pattern(search.strip())
            filters.append(
                Project.name.ilike(pattern, escape="\\")
                | Project.description.ilike(pattern, escape="\\")
            )

        page = (
            select(Project)
            .where(*filters)
            .order_by(*_order_by_clauses(sort, order))
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(page)
        rows = list(result.scalars().all())

        total = int(
            await self.session.scalar(select(func.count()).select_from(Project).where(*filters))
        )
        return rows, total

    async def update_fields(self, project: Project, **fields: object) -> Project:
        """Apply a partial update and persist it.

        Args:
            project: The row to update.
            **fields: The columns to write. Only the names in
                :data:`_UPDATABLE_FIELDS` are accepted. This method used to
                ``setattr`` whatever it was handed, which is safe exactly as long
                as every caller assembles its kwargs by hand and becomes a
                mass-assignment hole the moment one of them forwards a payload —
                ``**payload.model_dump()`` would then be able to write
                ``owner_id``. The allowlist moves that judgement from "does every
                current caller remember" to a single reviewable line. Absent keys
                are left untouched, so ``None`` is a value to write (clear the
                column) and not a way to skip one.

        Returns:
            The updated, refreshed row.

        Raises:
            ValueError: If a field is not on the allowlist. The name comes from
                code, not from a request body, so failing loudly beats writing a
                column nobody meant to write.
        """
        rejected = sorted(set(fields) - _UPDATABLE_FIELDS)
        if rejected:
            raise ValueError(
                f"Cannot write {', '.join(rejected)} on a project row; "
                f"update_fields accepts only {', '.join(sorted(_UPDATABLE_FIELDS))}."
            )
        for key, value in fields.items():
            setattr(project, key, value)
        self.session.add(project)
        await self.session.commit()
        await self.session.refresh(project)
        return project

    async def delete(self, project: Project) -> None:
        """Hard-delete the project row.

        A hard delete, not a soft one: a local-first product holds the user's own
        data, and an ``archived`` project is the state a user deliberately keeps.
        What happens to what pointed at this row is the database's job and is
        worth stating — ``tasks`` and ``project_tags`` cascade, because a task
        without its project has no meaning, while ``activity_events`` is set to
        ``NULL`` by its own ``ON DELETE SET NULL``, because deleting a project
        must not erase the record of what happened inside it.
        """
        await self.session.delete(project)
        await self.session.commit()

    async def count_for_user(self, owner_id: uuid.UUID) -> int:
        """Count the owner's projects.

        Counted in SQL rather than as ``len(await self.list_for_user(...))`` so
        the number costs the same whether the user has one project or five
        thousand.
        """
        result = await self.session.execute(
            select(func.count()).select_from(Project).where(Project.owner_id == owner_id)
        )
        return int(result.scalar_one())

    async def stats_for_user(self, owner_id: uuid.UUID) -> dict[str, int]:
        """Return every project's status bucket plus the total, in one query.

        One ``GROUP BY status`` rather than a loop of ``COUNT``s: a dashboard
        fires this on every render, and five separate counts is five round trips
        whose results can disagree with each other if a write lands between them.

        Every value of :class:`~app.models.enums.ProjectStatus` is present even
        when its bucket is empty, so a caller can read ``stats["archived"]``
        without a ``.get()`` default and without a bucket disappearing from the
        response when the first archived project is deleted. ``total`` is the sum
        of the buckets — the same rows, partitioned — rather than a second query
        that could disagree with the first.
        """
        result = await self.session.execute(
            select(Project.status, func.count())
            .where(Project.owner_id == owner_id)
            .group_by(Project.status)
        )
        stats: dict[str, int] = {status.value: 0 for status in ProjectStatus}
        for status_value, bucket in result.all():
            stats[str(status_value)] = int(bucket)
        stats["total"] = sum(value for key, value in stats.items() if key != "total")
        return stats
