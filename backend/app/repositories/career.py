"""Persistence for Phase 9: the career profile, its records, and its evidence.

Nothing this file writes is generated. Every column it puts in the database is a
transcription of something the user supplied, and the repository's own job is to
be the last place that could quietly invent one — so the write sets below are
short, closed, and say in a :class:`ValueError` what a caller was not allowed to
put in a row.

**Every read filters on ``user_id`` in the ``WHERE`` clause**, never by filtering
a loaded page afterwards, and a foreign id is answered with ``None`` or ``False``
rather than an exception. The route above turns ``None`` into a 404, and a 403
would confirm the id exists and turn the endpoint into a probe for which career
ids are real. A user's career history is the most identifying row set this
schema holds, which is exactly why the ownership predicate is never something a
caller supplies.

**The profile is an upsert, and it is an upsert because the column is unique.**
``career_profiles.user_id`` carries a unique constraint, so a person has one
career by definition and a second row would be a second *answer* to every
question about it. :meth:`CareerRepository.upsert_profile` therefore creates the
row if it is absent and edits it if it is present, in that order, and never
blindly inserts: the create step is an ``ON CONFLICT DO NOTHING`` whose only
product is the row's id, so two concurrent ``PUT``s cannot manufacture two
profiles and one cannot fail on the constraint. A blind ``INSERT`` would make a
second profile impossible in the same way, and would also make the *first*
``PUT`` for an account that already has a profile fail — which is the bug the
upsert exists to prevent.

**Evidence deduplication is one constraint and it leans on PostgreSQL's nulls.**
:meth:`CareerRepository.find_duplicate_evidence` exists because the route wants
to say "you already have this evidence" in a sentence a person can act on, and an
:class:`~sqlalchemy.exc.IntegrityError` carries a constraint name rather than a
message. The subtlety it has to reproduce exactly is that **nulls do not collide
in a btree unique index**: several manually-added ``ACHIEVEMENT`` rows all have
three null foreign keys, all compare unequal, and all coexist. So the check
short-circuits to "no duplicate" whenever any of the three identity columns is
null — because in that case the constraint *cannot* fire, and reporting a
conflict there would refuse a row the database was perfectly willing to store.
A naive equality lookup translated to ``IS NULL`` would get that exactly wrong
and would make the second manual achievement an error.

**The evidence foreign keys are ``SET NULL`` and the deletion methods do not
work around it.** Deleting a project must not delete the record that a project
was completed. The same reasoning gives ``learning_activities.goal_id`` its
``SET NULL`` upstream, and it is why :meth:`CareerRepository.delete_experience`
is a plain owner-scoped ``DELETE`` with no read of the children first: the
trail outlives the thing it records, and a repository that read the children to
delete them would be a repository that believed otherwise.

Smaller rules, inherited from Phase 8 rather than invented here: every method
commits on its own (there is no ``async with session.begin()`` in this
codebase), ``updated_at`` is written explicitly into every ``UPDATE`` because
:class:`~app.db.base.TimestampMixin`'s ``onupdate`` is something SQLAlchemy
applies to statements it generates itself, pages are clamped at both ends, and
a list describes a different set from the count printed beside it — so the page
and its total come from one statement wherever they can.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

from sqlalchemy import delete, func, literal, select, union_all, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.career import (
    DEFAULT_CAREER_EVIDENCE_SOURCE,
    CareerEvidence,
    CareerExperience,
    CareerProfile,
)
from app.models.enums import (
    CareerEvidenceType,
    validate_career_evidence_type,
    validate_career_record_kind,
)

__all__ = ["CareerRepository"]

#: Page size ceiling, and what a caller that says nothing gets. The request layer
#: validates ``?limit=``; this is the second lock on the same door rather than a
#: second policy, because a career timeline is a paginated list of rows and not
#: a report.
_MAX_PAGE_SIZE = 200
_DEFAULT_PAGE_SIZE = 50

#: Labels distinguishing the two band vocabularies in a combined tally read.
#: ``by_type`` is the closed :class:`~app.models.enums.CareerEvidenceType` set;
#: ``by_source`` is open, because a subsystem that has never run has no band to
#: appear in. They are labels on rows rather than two statements so both come
#: from one filter over one set of rows.
_BAND_BY_TYPE = "type"
_BAND_BY_SOURCE = "source"

#: The only columns a profile write may touch. ``user_id`` is absent because it
#: is the *key* of the upsert and the owner is not something a request may
#: choose; ``id`` and ``created_at`` are absent because neither is editable by
#: anybody.
_EDITABLE_PROFILE_COLUMNS: frozenset[str] = frozenset(
    {
        "headline",
        "links",
        "location",
        "summary",
        "target_domain",
        "target_role",
    }
)

#: The only columns an experience PATCH may write. Every one of them is a fact
#: the user supplied and may correct.
_EDITABLE_EXPERIENCE_COLUMNS: frozenset[str] = frozenset(
    {
        "description",
        "ended_on",
        "kind",
        "organisation",
        "started_on",
        "title",
        "url",
    }
)

#: The only columns an evidence PATCH may write. ``source`` and the three
#: foreign keys are included deliberately: correcting where a claim came from is
#: a legitimate correction, and if it collides with an existing row the
#: constraint says so rather than this layer guessing that it should not.
_EDITABLE_EVIDENCE_COLUMNS: frozenset[str] = frozenset(
    {
        "description",
        "evidence_type",
        "occurred_on",
        "project_id",
        "repository_id",
        "skill_id",
        "source",
        "title",
    }
)

#: Per-column validators applied to every write on a table, keyed by column
#: name. ``source`` is absent on purpose: it is an open vocabulary — ``manual``
#: or whatever subsystem derived the row — and a Phase 10 subsystem must be able
#: to name itself here without a migration.
_EXPERIENCE_VALIDATORS: Mapping[str, Callable[[Any], Any]] = {
    "kind": validate_career_record_kind,
}
_EVIDENCE_VALIDATORS: Mapping[str, Callable[[Any], Any]] = {
    "evidence_type": validate_career_evidence_type,
}

#: The three nullable columns of ``uq_career_evidence_source_identity``. A row
#: that leaves any of them null cannot collide with anything, because nulls do
#: not collide in a btree unique index — see the module docstring and
#: :meth:`CareerRepository.find_duplicate_evidence`.
_EVIDENCE_IDENTITY_COLUMNS: tuple[str, ...] = ("project_id", "skill_id", "repository_id")


def _bounded(limit: int, offset: int) -> tuple[int, int]:
    """Clamp a page request to something a paginated list can serve.

    Both ends are clamped rather than rejected: the request layer is where
    ``?limit=500`` is a 422 and this is the belt to those braces. ``limit`` is
    floored at one because ``LIMIT 0`` returns nothing and a list route
    answering with an empty page for every request would look like an outage.
    """
    return max(1, min(int(limit), _MAX_PAGE_SIZE)), max(0, int(offset))


def _evidence_filters(
    owner_id: uuid.UUID,
    *,
    evidence_type: str | None = None,
    project_id: uuid.UUID | None = None,
    skill_id: uuid.UUID | None = None,
    repository_id: uuid.UUID | None = None,
    since: date | None = None,
    until: date | None = None,
) -> list[Any]:
    """The ``WHERE`` clauses selecting one owner's matching evidence.

    One builder for :meth:`CareerRepository.list_evidence` and
    :meth:`CareerRepository.evidence_tally`, because the page and the counts
    printed beside it have to describe the same set. Two copies of these seven
    clauses would be two answers that can drift apart the first time a filter is
    added to one of them and forgotten in the other — and a header that counts a
    different set from the list under it is worse than no header.

    ``owner_id`` is a predicate here and never an argument a caller can widen:
    every read of this table goes through this function, so there is no signature
    in this repository that can be asked for somebody else's evidence.
    """
    filters: list[Any] = [CareerEvidence.user_id == owner_id]
    if evidence_type is not None:
        filters.append(
            CareerEvidence.evidence_type == validate_career_evidence_type(evidence_type).value
        )
    if project_id is not None:
        filters.append(CareerEvidence.project_id == project_id)
    if skill_id is not None:
        filters.append(CareerEvidence.skill_id == skill_id)
    if repository_id is not None:
        filters.append(CareerEvidence.repository_id == repository_id)
    if since is not None:
        filters.append(CareerEvidence.occurred_on >= since)
    if until is not None:
        filters.append(CareerEvidence.occurred_on < until)
    return filters


def _reject_unknown_columns(
    values: Mapping[str, Any], allowed: frozenset[str], *, what: str
) -> None:
    """Refuse a write to a column outside the set the caller was given.

    Raises:
        ValueError: If ``values`` names a column outside ``allowed``. Every such
            set is the *definition* of what the corresponding write may touch, so
            a key outside it is a programming error, and reporting it here beats
            a silent no-op or an ``IntegrityError`` from storage with no mention
            of which field was wrong.
    """
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"{what} may only write {', '.join(sorted(allowed))}; got {unknown}.")


def _validated_writes(
    values: Mapping[str, Any],
    allowed: frozenset[str],
    validators: Mapping[str, Callable[[Any], Any]],
    *,
    what: str,
) -> dict[str, Any]:
    """Normalise one write mapping: scope, then vocabulary.

    Every update path on the two dated tables funnels through here, which is the
    only way to guarantee that a record patched through one method is checked
    exactly as one created through another. Unknown keys raise; values with a
    closed vocabulary are coerced through the ``validate_*`` helpers and stored
    as their plain string, because these columns are ``String(n)`` and never a
    native PostgreSQL enum.

    A key mapped to ``None`` is written as SQL ``NULL``, which is how a
    description or an organisation is cleared.

    Args:
        values: Column name to new value, as the caller supplied it.
        allowed: The columns this write may touch.
        validators: Per-column coercion. A column absent from the mapping is
            passed through unvalidated.
        what: Human name of the write, used in every message.

    Returns:
        A new mapping carrying validated values, ready to be splatted into
        ``UPDATE ... VALUES``.

    Raises:
        ValueError: If the mapping is empty, names a column outside ``allowed``,
            or carries a value the vocabulary does not have.
    """
    if not values:
        raise ValueError(f"{what} must change at least one field.")
    _reject_unknown_columns(values, allowed, what=what)
    writes: dict[str, Any] = {}
    for key, value in values.items():
        validator = validators.get(key)
        writes[key] = validator(value).value if validator is not None else value
    return writes


class CareerRepository:
    """Persistence for Phase 9: the profile, its dated records, and its evidence.

    Every read is owner-scoped and every write carries the owner id explicitly,
    including the profile upsert, whose conflict target *is* the owner — so a
    caller cannot write a row onto another account by passing a mismatched id.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Profile
    # ------------------------------------------------------------------

    async def get_profile(self, owner_id: uuid.UUID) -> CareerProfile | None:
        """This account's profile, or ``None`` when it has never been written.

        ``None`` is a real state and not an error: a new account has a profile
        with nothing on it yet, and the route renders it as an empty profile
        rather than inventing one.
        """
        result = await self.session.execute(
            select(CareerProfile).where(CareerProfile.user_id == owner_id)
        )
        return result.scalar_one_or_none()

    async def upsert_profile(self, owner_id: uuid.UUID, values: Mapping[str, Any]) -> CareerProfile:
        """Write the one profile this account has, creating it if it is absent.

        **Get-or-create, then update** — never a blind ``INSERT``. The create
        step is an ``INSERT ... ON CONFLICT DO NOTHING`` against the unique
        ``user_id`` whose only product is the row's id, so it is a no-op for an
        account that already has a profile and a creation for one that does not,
        with no window in which two concurrent requests could both insert. The
        edit is then a single owner-scoped ``UPDATE ... RETURNING`` against that
        id, so the write cannot land on a row belonging to anybody else.

        Only :data:`_EDITABLE_PROFILE_COLUMNS` is writable. Every one of them is
        something the user typed; there is no field here NEXUS may fill in, and
        a caller that reaches for one is told so by name rather than being
        quietly ignored. ``links`` is written as given — an empty list is the
        user having supplied no URLs, which is an answer and not an absence.

        Args:
            owner_id: Whose profile this is. It is also the upsert key.
            values: Column name to new value, from
                :data:`_EDITABLE_PROFILE_COLUMNS`.

        Returns:
            The stored row, refreshed so ``created_at`` — which the create step
            defaulted on the server — carries the database's answer.

        Raises:
            ValueError: If ``values`` is empty or names a column outside the
                editable set.
        """
        if not values:
            raise ValueError("A profile write must change at least one field.")
        _reject_unknown_columns(values, _EDITABLE_PROFILE_COLUMNS, what="A profile write")

        created = await self.session.execute(
            pg_insert(CareerProfile)
            .values(id=uuid.uuid4(), user_id=owner_id, links=[])
            .on_conflict_do_nothing(index_elements=[CareerProfile.user_id])
            .returning(CareerProfile.id)
        )
        profile_id = created.scalar_one_or_none()
        if profile_id is None:
            profile_id = await self.session.scalar(
                select(CareerProfile.id).where(CareerProfile.user_id == owner_id)
            )

        writes = {**values, "updated_at": func.now()}
        statement = (
            update(CareerProfile)
            .where(CareerProfile.id == profile_id, CareerProfile.user_id == owner_id)
            .values(**writes)
            .returning(CareerProfile)
        )
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        row = result.scalar_one_or_none()
        await self.session.commit()
        if row is None:
            # Unreachable: the statement above either created the row or found
            # the existing one, and nothing between them can have removed it.
            # Kept because an upsert that returned ``None`` would be a second
            # profile's worth of ambiguity, and that is exactly what this
            # method exists to make impossible.
            raise RuntimeError("The career profile upsert found no row to write.")
        return row

    # ------------------------------------------------------------------
    # Experience
    # ------------------------------------------------------------------

    async def list_experience(
        self,
        owner_id: uuid.UUID,
        *,
        kind: str | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[list[CareerExperience], int]:
        """This owner's dated records and the total the filters match.

        Ordered dated-first, then current-before-finished, then by start date
        descending, then by ``id``. "What am I doing now" is the question a
        career page opens with, so the current role leads; and a record with no
        start date sorts *last*, because "no start date" is not "started longer
        ago than anything else" — treating an undated self-directed course as
        the most ancient thing on the page is a statement the data does not
        support, and it would put the newest addition at the bottom.

        The page and its total come from one statement — a window count over the
        same rows — so the header and the list cannot describe two snapshots. The
        empty-page case has no row to carry the window count and is counted
        separately, re-asserting the owner predicate.

        Args:
            owner_id: Whose records to list.
            kind: Narrow to one :class:`~app.models.enums.CareerRecordKind`
                member, or ``None`` for the whole timeline.
            limit: Page size, clamped to :data:`_MAX_PAGE_SIZE`.
            offset: Rows to skip, floored at zero.

        Returns:
            The page of rows and the total number of rows the filters match.
        """
        page_size, skip = _bounded(limit, offset)
        filters: list[Any] = [CareerExperience.user_id == owner_id]
        if kind is not None:
            filters.append(CareerExperience.kind == validate_career_record_kind(kind).value)

        statement = (
            select(CareerExperience, func.count().over().label("total"))
            .where(*filters)
            .order_by(
                CareerExperience.started_on.is_(None).asc(),
                CareerExperience.ended_on.is_not(None).asc(),
                CareerExperience.started_on.desc(),
                CareerExperience.id.desc(),
            )
            .limit(page_size)
            .offset(skip)
        )
        rows = list((await self.session.execute(statement)).all())
        if rows:
            return [row[0] for row in rows], int(rows[0].total)
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(CareerExperience).where(*filters)
            )
        )
        return [], total

    async def get_experience(
        self, owner_id: uuid.UUID, experience_id: uuid.UUID
    ) -> CareerExperience | None:
        """One dated record, or ``None`` if it is not this owner's.

        Owner-scoped like every other single-row read, so a foreign id and an
        unknown id are the same answer and the route can turn both into a 404.
        """
        result = await self.session.execute(
            select(CareerExperience).where(
                CareerExperience.id == experience_id,
                CareerExperience.user_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def create_experience(
        self,
        owner_id: uuid.UUID,
        *,
        kind: str,
        title: str,
        organisation: str | None = None,
        started_on: date | None = None,
        ended_on: date | None = None,
        description: str | None = None,
        url: str | None = None,
    ) -> CareerExperience:
        """Record one dated line the user supplied.

        Every value here is a transcription: the ``title`` the user wrote, the
        ``organisation`` they named or left null, the dates they gave. There is
        no lookup behind any of them — no university register, no payroll, no
        scraped profile — and ``organisation`` being null is the normal case for
        a self-directed project rather than a gap to be filled later.

        ``ended_on=None`` is how a current role or an ongoing degree says so,
        which is a different fact from "finished on an unknown date". The
        ordering constraint is the database's, and this method does not soften
        it: a range that runs backwards is refused at the table.

        Args:
            owner_id: Whose record this is.
            kind: A :class:`~app.models.enums.CareerRecordKind` member.
            title: The user's own name for the record.
            organisation: Who issued it or where it was done. Nullable.
            started_on: When it began, if the user said.
            ended_on: When it ended. ``None`` means current.
            description: The user's description of what it involved.
            url: A link the user supplied. Never fetched.

        Returns:
            The stored row, refreshed so its server defaults carry the database's
            answer.

        Raises:
            ValueError: If ``kind`` is outside the vocabulary.

        Raises:
            :class:`~sqlalchemy.exc.IntegrityError`: If ``ended_on`` precedes
                ``started_on``, which ``ck_career_experience_dates_in_order``
                refuses at the table.
        """
        record = CareerExperience(
            id=uuid.uuid4(),
            user_id=owner_id,
            kind=validate_career_record_kind(kind).value,
            title=title,
            organisation=organisation,
            started_on=started_on,
            ended_on=ended_on,
            description=description,
            url=url,
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def update_experience(
        self, owner_id: uuid.UUID, experience_id: uuid.UUID, values: Mapping[str, Any]
    ) -> CareerExperience | None:
        """Correct one dated record, in a single owner-scoped statement.

        An ``UPDATE ... RETURNING`` rather than a read followed by a write, so
        another account's row is never loaded, only *not* updated — and the
        returned ``None`` is what the route turns into a 404. A key mapped to
        ``None`` is written as SQL ``NULL``, which is how an end date or an
        organisation is cleared.

        Args:
            owner_id: Whose record this is.
            experience_id: The record to edit.
            values: Column name to new value, from
                :data:`_EDITABLE_EXPERIENCE_COLUMNS`.

        Returns:
            The updated row, or ``None`` when no row matched — a foreign or
            unknown id, answered identically to a foreign one.

        Raises:
            ValueError: If ``values`` is empty, names a column outside the
                editable set, or carries an unknown ``kind``.

        Raises:
            :class:`~sqlalchemy.exc.IntegrityError`: If the edit would leave
                ``ended_on`` before ``started_on``.
        """
        writes = _validated_writes(
            values, _EDITABLE_EXPERIENCE_COLUMNS, _EXPERIENCE_VALIDATORS, what="An experience edit"
        )
        writes["updated_at"] = func.now()
        statement = (
            update(CareerExperience)
            .where(
                CareerExperience.id == experience_id,
                CareerExperience.user_id == owner_id,
            )
            .values(**writes)
            .returning(CareerExperience)
        )
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        row = result.scalar_one_or_none()
        await self.session.commit()
        return row

    async def delete_experience(self, owner_id: uuid.UUID, experience_id: uuid.UUID) -> bool:
        """Remove one dated record.

        A plain owner-scoped ``DELETE``. There is no read of the evidence rows
        that may name this record first, because those foreign keys are
        ``SET NULL``: the claim the user made about it survives as their own
        claim, with the pointer dropped. A repository that read them to delete
        them would be inverting the whole point of the table.

        Args:
            owner_id: Whose record this is.
            experience_id: The record to delete.

        Returns:
            ``True`` when a row was deleted, ``False`` when none matched.
        """
        result = await self.session.execute(
            delete(CareerExperience).where(
                CareerExperience.id == experience_id,
                CareerExperience.user_id == owner_id,
            )
        )
        await self.session.commit()
        return bool(result.rowcount)

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    async def count_evidence(self, owner_id: uuid.UUID) -> int:
        """How many pieces of evidence this owner has recorded.

        The check behind ``career_max_evidence``, answerable before the insert
        rather than by counting rows afterwards and reporting a conflict nobody
        can act on. It counts every row, manual and derived alike: an evidence
        row the user typed occupies its slot the same way a project-derived one
        does.

        Args:
            owner_id: Whose evidence to count.

        Returns:
            The count, zero for an owner with none.
        """
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(CareerEvidence)
                .where(CareerEvidence.user_id == owner_id)
            )
        )

    async def list_evidence(
        self,
        owner_id: uuid.UUID,
        *,
        evidence_type: str | None = None,
        project_id: uuid.UUID | None = None,
        skill_id: uuid.UUID | None = None,
        repository_id: uuid.UUID | None = None,
        since: date | None = None,
        until: date | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[list[CareerEvidence], int]:
        """This owner's evidence, newest first, and the total the filters match.

        Ordered by ``occurred_on`` descending with ``id`` as a tiebreaker, so
        paging is stable: ``occurred_on`` is a date and two achievements can
        share one exactly.

        The date window is half-open, matching the learning window: ``since`` is
        inclusive and ``until`` exclusive, so two adjacent windows cannot both
        count evidence dated on the boundary.

        Args:
            owner_id: Whose evidence to list.
            evidence_type: Narrow to one
                :class:`~app.models.enums.CareerEvidenceType` member.
            project_id: Narrow to the evidence drawn from one project. Evidence
                with no project is excluded rather than folded into it.
            skill_id: Narrow to the evidence drawn from one skill.
            repository_id: Narrow to the evidence drawn from one repository.
            since: Inclusive lower bound on ``occurred_on``.
            until: Exclusive upper bound on ``occurred_on``.
            limit: Page size, clamped to :data:`_MAX_PAGE_SIZE`.
            offset: Rows to skip, floored at zero.

        Returns:
            The page of rows and the total number of rows the filters match.
        """
        page_size, skip = _bounded(limit, offset)
        filters = _evidence_filters(
            owner_id,
            evidence_type=evidence_type,
            project_id=project_id,
            skill_id=skill_id,
            repository_id=repository_id,
            since=since,
            until=until,
        )

        statement = (
            select(CareerEvidence, func.count().over().label("total"))
            .where(*filters)
            .order_by(CareerEvidence.occurred_on.desc(), CareerEvidence.id.desc())
            .limit(page_size)
            .offset(skip)
        )
        rows = list((await self.session.execute(statement)).all())
        if rows:
            return [row[0] for row in rows], int(rows[0].total)
        total = int(
            await self.session.scalar(
                select(func.count()).select_from(CareerEvidence).where(*filters)
            )
        )
        return [], total

    async def evidence_tally(
        self,
        owner_id: uuid.UUID,
        *,
        evidence_type: str | None = None,
        project_id: uuid.UUID | None = None,
        skill_id: uuid.UUID | None = None,
        repository_id: uuid.UUID | None = None,
        since: date | None = None,
        until: date | None = None,
    ) -> dict[str, Any]:
        """Whole-set counts over the matching evidence rows, in **two statements**.

        These are the figures printed beside the page in
        :meth:`list_evidence` and carried by the summary and the feature vector. They
        describe **every** matching row, never the page, and they are aggregates over
        the same rows rather than a second opinion read differently.

        **Two statements, not a walk.** The obvious way to produce these is to page
        the whole set and count in Python, and that is what this method replaces: it
        cost one round trip per 200 rows, so an account with 450 evidence rows spent
        three statements to learn how many there were — and the career page asks for
        this tally three times (summary, list, features), so nine statements where
        two suffice, growing with the account and never with anything else. Here the
        band counts come from two grouped reads the database runs over an index and
        the distinct/derived figures from one aggregate row. The cost is two
        statements and a handful of rows back whatever the account holds.

        The two vocabularies are read in one statement because a band count needs a
        ``GROUP BY`` on a different column each time and the answer has to come from
        the *same* filter; ``UNION ALL`` of two grouped selects over one set of
        predicates is that, with a literal label to say which vocabulary a row
        belongs to.

        Args:
            owner_id: Whose evidence to count.
            evidence_type: Narrow to one evidence type.
            project_id: Narrow to the evidence drawn from one project.
            skill_id: Narrow to the evidence drawn from one skill.
            repository_id: Narrow to the evidence drawn from one repository.
            since: Inclusive lower bound on ``occurred_on``.
            until: Exclusive upper bound.

        Returns:
            A mapping with ``by_type`` and ``by_source`` band counts, ``total``,
            ``manual_count``, ``skill_linked_count``, ``skills_with_evidence``,
            ``linked_project_count`` and ``latest_evidence_on``. Every count is a
            real count of a set that was searched and found empty, and
            ``latest_evidence_on`` is ``None`` — never today's date — when nothing
            matched.
        """
        filters = _evidence_filters(
            owner_id,
            evidence_type=evidence_type,
            project_id=project_id,
            skill_id=skill_id,
            repository_id=repository_id,
            since=since,
            until=until,
        )
        by_type = (
            select(
                literal(_BAND_BY_TYPE).label("vocabulary"),
                CareerEvidence.evidence_type.label("band"),
                func.count().label("rows"),
            )
            .where(*filters)
            .group_by(CareerEvidence.evidence_type)
        )
        by_source = (
            select(
                literal(_BAND_BY_SOURCE).label("vocabulary"),
                CareerEvidence.source.label("band"),
                func.count().label("rows"),
            )
            .where(*filters)
            .group_by(CareerEvidence.source)
        )
        counts: dict[str, dict[str, int]] = {
            _BAND_BY_TYPE: {},
            _BAND_BY_SOURCE: {},
        }
        for vocabulary, band, rows in (
            await self.session.execute(union_all(by_type, by_source))
        ).all():
            counts[str(vocabulary)][str(band)] = int(rows)

        row = (
            await self.session.execute(
                select(
                    func.count(),
                    func.count().filter(CareerEvidence.source == DEFAULT_CAREER_EVIDENCE_SOURCE),
                    func.count().filter(CareerEvidence.skill_id.is_not(None)),
                    func.count(func.distinct(CareerEvidence.skill_id)),
                    func.count(func.distinct(CareerEvidence.project_id)),
                    func.max(CareerEvidence.occurred_on),
                ).where(*filters)
            )
        ).one()
        return {
            "by_type": counts[_BAND_BY_TYPE],
            "by_source": counts[_BAND_BY_SOURCE],
            "total": int(row[0]),
            "manual_count": int(row[1]),
            "skill_linked_count": int(row[2]),
            "skills_with_evidence": int(row[3]),
            "linked_project_count": int(row[4]),
            "latest_evidence_on": row[5],
        }

    async def get_evidence(
        self, owner_id: uuid.UUID, evidence_id: uuid.UUID
    ) -> CareerEvidence | None:
        """One evidence row, or ``None`` if it is not this owner's.

        Owner-scoped like every other single-row read, so a foreign id and an
        unknown id are the same answer and the route can turn both into a 404.
        """
        result = await self.session.execute(
            select(CareerEvidence).where(
                CareerEvidence.id == evidence_id,
                CareerEvidence.user_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_duplicate_evidence(
        self,
        owner_id: uuid.UUID,
        *,
        evidence_type: CareerEvidenceType | str,
        source: str = DEFAULT_CAREER_EVIDENCE_SOURCE,
        project_id: uuid.UUID | None = None,
        skill_id: uuid.UUID | None = None,
        repository_id: uuid.UUID | None = None,
    ) -> CareerEvidence | None:
        """The row ``uq_career_evidence_source_identity`` would refuse, or ``None``.

        The check behind "you already have this evidence", asked so a route can
        answer in a sentence instead of surfacing a constraint name. It is
        **not** a general identity lookup: it answers precisely the question the
        constraint answers, which means it has to reproduce the constraint's own
        treatment of nulls.

        Nulls do not collide in a btree unique index, so a row that leaves any of
        :data:`_EVIDENCE_IDENTITY_COLUMNS` null **cannot** collide with anything
        and this returns ``None`` without querying. That is the whole reason the
        second manual ``ACHIEVEMENT`` row is allowed to exist — both of its
        foreign keys are null, both rows compare unequal, and neither is a
        duplicate. A lookup written with ``IS NULL`` for the absent columns would
        return the first one and refuse the second, which would be a repository
        contradicting its own schema.

        A conflict is therefore only possible when all three are present, and
        then only if the type and the source match too. Owner-scoped throughout:
        another account's evidence is the same identity to a different person.

        Args:
            owner_id: Whose evidence to look for.
            evidence_type: The :class:`~app.models.enums.CareerEvidenceType`
                member the candidate row would carry.
            source: The subsystem that derived it, or ``manual``.
            project_id: The project it was drawn from.
            skill_id: The skill it was drawn from.
            repository_id: The repository it was drawn from.

        Returns:
            The conflicting row, or ``None`` when the insert would be accepted.
        """
        identity = {
            "project_id": project_id,
            "skill_id": skill_id,
            "repository_id": repository_id,
        }
        if any(identity[name] is None for name in _EVIDENCE_IDENTITY_COLUMNS):
            return None

        result = await self.session.execute(
            select(CareerEvidence).where(
                CareerEvidence.user_id == owner_id,
                CareerEvidence.evidence_type == validate_career_evidence_type(evidence_type).value,
                CareerEvidence.source == source,
                *(
                    getattr(CareerEvidence, name) == value
                    for name, value in identity.items()
                    if value is not None
                ),
            )
        )
        return result.scalar_one_or_none()

    async def create_evidence(
        self,
        owner_id: uuid.UUID,
        *,
        evidence_type: CareerEvidenceType | str,
        title: str,
        occurred_on: date,
        description: str | None = None,
        project_id: uuid.UUID | None = None,
        skill_id: uuid.UUID | None = None,
        repository_id: uuid.UUID | None = None,
        source: str = DEFAULT_CAREER_EVIDENCE_SOURCE,
    ) -> CareerEvidence:
        """Record one thing the user wants to be able to point at.

        The row is a *claim with a provenance*: ``title`` is the user's own line
        rather than something generated from the linked record, and ``source``
        says whether they wrote it or a subsystem derived it. A manually-added
        achievement has all three foreign keys null, which is a legitimate row
        and not an incomplete one.

        ``occurred_on`` is required and is a plain date: evidence is a thing that
        happened on a day, and a placeholder date would be a fabricated one.

        Args:
            owner_id: Whose evidence this is.
            evidence_type: A :class:`~app.models.enums.CareerEvidenceType`
                member. ``repository_activity`` means commits were observed —
                not that a task was completed.
            title: The user's line for it.
            occurred_on: The day it happened.
            description: Optional user note.
            project_id: The project it was drawn from.
            skill_id: The skill it was drawn from.
            repository_id: The repository it was drawn from.
            source: ``manual``, or the subsystem that derived the row. An open
                vocabulary, and part of the uniqueness key — so a typo here is
                the one mistake on this table that would quietly allow a
                duplicate, which is why the default is the module's single
                spelling of it.

        Returns:
            The stored row, refreshed so its server defaults carry the database's
            answer.

        Raises:
            ValueError: If ``evidence_type`` is outside the vocabulary.

        Raises:
            :class:`~sqlalchemy.exc.IntegrityError`: If a row with the same
                identity already exists. Callers that want the friendly wording
                ask :meth:`find_duplicate_evidence` first.
        """
        evidence = CareerEvidence(
            id=uuid.uuid4(),
            user_id=owner_id,
            evidence_type=validate_career_evidence_type(evidence_type).value,
            title=title,
            description=description,
            occurred_on=occurred_on,
            project_id=project_id,
            skill_id=skill_id,
            repository_id=repository_id,
            source=source,
        )
        self.session.add(evidence)
        await self.session.commit()
        await self.session.refresh(evidence)
        return evidence

    async def update_evidence(
        self, owner_id: uuid.UUID, evidence_id: uuid.UUID, values: Mapping[str, Any]
    ) -> CareerEvidence | None:
        """Correct one evidence row, in a single owner-scoped statement.

        An ``UPDATE ... RETURNING`` rather than a read followed by a write, so
        another account's row is never loaded, only *not* updated — and the
        returned ``None`` is what the route turns into a 404. A key mapped to
        ``None`` is written as SQL ``NULL``, which is how a project link is
        dropped while the claim itself stays.

        ``title`` is deliberately writable and deliberately *not* part of the
        uniqueness key: a rename must stay a rename rather than become a second
        row.

        Args:
            owner_id: Whose evidence this is.
            evidence_id: The evidence to edit.
            values: Column name to new value, from
                :data:`_EDITABLE_EVIDENCE_COLUMNS`.

        Returns:
            The updated row, or ``None`` when no row matched — a foreign or
            unknown id, answered identically to a foreign one.

        Raises:
            ValueError: If ``values`` is empty, names a column outside the
                editable set, or carries an unknown ``evidence_type``.

        Raises:
            :class:`~sqlalchemy.exc.IntegrityError`: If the edit would move the
                row onto an identity another row already holds.
        """
        writes = _validated_writes(
            values, _EDITABLE_EVIDENCE_COLUMNS, _EVIDENCE_VALIDATORS, what="An evidence edit"
        )
        writes["updated_at"] = func.now()
        statement = (
            update(CareerEvidence)
            .where(
                CareerEvidence.id == evidence_id,
                CareerEvidence.user_id == owner_id,
            )
            .values(**writes)
            .returning(CareerEvidence)
        )
        result = await self.session.execute(statement.execution_options(populate_existing=True))
        row = result.scalar_one_or_none()
        await self.session.commit()
        return row

    async def delete_evidence(self, owner_id: uuid.UUID, evidence_id: uuid.UUID) -> bool:
        """Remove one evidence row.

        The evidence table has no children to worry about, so this is one
        statement: the claim itself, or nothing. Owner-scoped, so a foreign id
        deletes nothing and reports the same ``False`` an unknown id does.

        Args:
            owner_id: Whose evidence this is.
            evidence_id: The evidence to delete.

        Returns:
            ``True`` when a row was deleted, ``False`` when none matched.
        """
        result = await self.session.execute(
            delete(CareerEvidence).where(
                CareerEvidence.id == evidence_id,
                CareerEvidence.user_id == owner_id,
            )
        )
        await self.session.commit()
        return bool(result.rowcount)
