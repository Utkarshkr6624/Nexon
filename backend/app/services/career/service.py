"""Phase 9 career intelligence: what the user has written, counted and never invented.

What this module is for
-----------------------
The career page has no arithmetic worth extracting. Where Phase 9's learning side
splits a pure metrics module out of its service, the figures here are counts over
rows the user typed or a subsystem recorded: ``len(tally)``, a distinct count, and
one window. :mod:`app.services.learning.gaps` earns its own module because a skill
gap is a *judgement* that could drift — a level, a confidence, a sentence about how
far someone is from where they said they wanted to be — and putting that judgement
where it can be asserted without a database is what stops it drifting. There is no
such judgement on this page, so there is nothing to isolate and a pure module here
would be a module of ``len()`` calls that gives the honesty rules nowhere to live.

**The honesty rules on this page are negative, so they belong where the writes are.**
They are all refusals: refuse to write a second profile, refuse a duplicate evidence
row with a sentence rather than a constraint name, refuse an end date before a start
date, refuse another account's row with a 404, and refuse to render a figure that
could not be computed as a zero. A refusal is a property of the code that *performs*
the write — it is not a function you can call, and it cannot be extracted from the
service without leaving the write behind. So this module is one file, and its
docstring is where the argument belongs.

What the service owns
---------------------
* **The profile upsert.** :meth:`CareerIntelligenceService.upsert_profile` is keyed
  on the unique ``career_profiles.user_id``. It is a get-or-create and then an edit,
  never a blind insert — a blind insert would make the *first* ``PUT`` fail for any
  account that already has a profile, which is the bug the upsert exists to prevent.
  The repository enforces the uniqueness and this method simply never asks for
  anything else.
* **Dated records and evidence**, both owner-scoped in every read. Another account's
  row is the same ``NotFoundError`` as an id nobody ever issued, word for word, so
  the endpoints are not an existence oracle.
* **The two reads the contract names**: :meth:`CareerIntelligenceService.summary` and
  :meth:`CareerIntelligenceService.features`.

Why the tallies are swept rather than aggregated in SQL
------------------------------------------------------
The list shapes promise that ``by_kind``, ``current_count``, ``by_type``,
``by_source`` and ``manual_count`` describe **every matching row**, not the page
beside them. :class:`~app.repositories.career.CareerRepository` returns the page and
its total and no tallies, so the tallies are computed by reading the matching rows
through the very repository method that produces the page — same method, same
filters, same owner predicate. The alternative was to restate the repository's
filter construction here, and a second copy of a ``WHERE`` clause is a second answer
that can silently disagree with the list above it.

The sweep is bounded on both ends, deliberately:

* the page size matches the repository's own ceiling, so a page is a page the
  repository would have served anyway;
* the row count is capped — at ``career_max_evidence`` for evidence (which the write
  path enforces, so the cap *is* the table size) and at
  :data:`_TIMELINE_SWEEP_ROW_LIMIT` for the timeline, which has no cap of its own. A
  set larger than the sweep limit is **under-counted rather than guessed at**, and
  the constant says so. A career timeline that long is a data-entry problem the user
  should see, not a number NEXUS should invent the tail of.

Why the window is anchored on the database clock
------------------------------------------------
Every in-window figure here is resolved from ``func.now()``, never
``datetime.now()``. A host whose clock drifts from the server's would file evidence
on the wrong day, and the day a thing happened is precisely what a windowed count
measures. The window itself is read from ``learning_default_window_days`` and
``learning_max_window_days`` because the frozen configuration of §3 defines no
career-specific window: the two pages share one window vocabulary rather than
declaring two ceilings for the same question.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.career import DEFAULT_CAREER_EVIDENCE_SOURCE
from app.models.developer import GitCommit, GitRepository
from app.models.enums import (
    ActivityEvent,
    CareerEvidenceType,
    CareerRecordKind,
    ProjectStatus,
    validate_career_evidence_type,
    validate_career_record_kind,
)
from app.models.project import Project
from app.repositories.career import CareerRepository
from app.repositories.developer import DeveloperRepository
from app.repositories.learning import LearningRepository
from app.repositories.project import ProjectRepository
from app.schemas.career import (
    CAREER_FEATURE_SCHEMA_VERSION,
    CareerEvidenceListRead,
    CareerEvidenceRead,
    CareerExperienceListRead,
    CareerExperienceRead,
    CareerFeatureValues,
    CareerFeatureVectorRead,
    CareerProfileRead,
    CareerSummaryRead,
)
from app.schemas.recommendation import band_count_sentence
from app.services.activity_service import ActivityService

if TYPE_CHECKING:
    from app.models.user import User

__all__ = ["CareerIntelligenceService"]

#: The page size a career list gets when a caller names none. Matches
#: :mod:`app.repositories.career`'s own default so the service's fallback and the
#: repository's are the same number rather than two that can drift.
_DEFAULT_PAGE_SIZE = 50
#: The page size the internal tally sweep reads with. It matches the repository's own
#: ceiling, which is the largest page the repository will serve — asking for more
#: would return the same rows with a silent clamp.
_SWEEP_PAGE_SIZE = 200
#: How many timeline rows one sweep will read before it stops counting.
#:
#: ``career_experience`` has no cap of its own: a user may legitimately hold a long
#: employment history, and refusing the hundred-and-first row would be a worse
#: answer than a bounded read. Past this limit the ``by_kind`` tally and the
#: ``current_count`` beside it **under-count rather than guess**, which is the same
#: answer the rest of this codebase gives when a number cannot be measured.
_TIMELINE_SWEEP_ROW_LIMIT = 1000

#: The evidence types, in the order a header sentence walks them: the five a
#: subsystem may derive first, then the two only a person can supply, so a sentence
#: built from these cannot put a certificate the user typed above a shipped feature.
_EVIDENCE_TYPE_ORDER: tuple[str, ...] = tuple(member.value for member in CareerEvidenceType)
#: The three record shapes, in the same spirit: the timeline reads education, work
#: and certifications in that order.
_RECORD_KIND_ORDER: tuple[str, ...] = tuple(member.value for member in CareerRecordKind)
#: The plural nouns the two list headers count. Passed to
#: :func:`app.schemas.recommendation.band_count_sentence` rather than written here, so
#: the header sentence is composed by the one function in this codebase whose job is
#: composing a sentence out of banded counts.
_EVIDENCE_LIST_SUBJECT = "career evidence records"
_RECORD_LIST_SUBJECT = "career records"

_EXPERIENCE_NOT_FOUND = "That career record was not found."
_EVIDENCE_NOT_FOUND = "That career evidence was not found."
_PROJECT_NOT_FOUND = "That project was not found."
_SKILL_NOT_FOUND = "That skill was not found."
_REPOSITORY_NOT_FOUND = "That repository was not found."
_DUPLICATE_EVIDENCE = "That evidence is already recorded from the same source."
_DATES_OUT_OF_ORDER = "An ended date may not be earlier than the started date."
_UNDATED_EVIDENCE = "Career evidence must carry the date it happened on."


def _as_utc(value: datetime | None) -> datetime | None:
    """Read a naive instant as UTC; leave an aware one alone.

    The same rule :mod:`app.repositories.developer` applies before writing a
    ``timestamptz``. Left naive, the same window boundary would resolve to a
    different instant depending on which connection read it, and the day an
    evidence row lands in is one of the figures this page prints.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _EvidenceTally:
    """Whole-set counts over one owner's evidence, for the header beside a list.

    Every field describes **every matching row**, never the page. ``by_type`` is
    completed against the closed vocabulary by
    :class:`~app.schemas.career.CareerEvidenceListRead` itself, and ``by_source`` is
    deliberately not: the source vocabulary is open, so a subsystem that has never run
    has no band to zero-fill.
    """

    #: Counts keyed by :class:`~app.models.enums.CareerEvidenceType` member value.
    by_type: dict[str, int] = field(default_factory=dict)
    #: Counts keyed by the subsystem each row came from. Open vocabulary.
    by_source: dict[str, int] = field(default_factory=dict)
    #: Rows the user entered by hand. The provenance figure: how much of the career
    #: page was written by the person rather than derived by the system.
    manual_count: int = 0
    #: Evidence rows pointing at a tracked skill, across every match.
    skill_linked_count: int = 0
    #: Distinct skills those rows name. A skill can carry several rows and is counted
    #: once, so this is never the row count above.
    skills_with_evidence: int = 0
    #: Distinct projects the rows name. Every foreign key is ``SET NULL``, so this
    #: counts rows naming a project whether or not it still exists — which is the
    #: point of the claim outliving the thing it claims.
    linked_project_count: int = 0
    #: The most recent day any matching row is dated, or ``None`` when there are none.
    latest_evidence_on: date | None = None


@dataclass(frozen=True, slots=True)
class _TimelineTally:
    """Whole-set counts over one owner's dated records.

    ``current_count`` is the other half of the date story: rows with no end date,
    which is how the profile says "this is ongoing". Both fields describe every
    matching record, not the page that carries them.
    """

    #: Counts keyed by :class:`~app.models.enums.CareerRecordKind` member value.
    by_kind: dict[str, int] = field(default_factory=dict)
    #: Matching records with no end date.
    current_count: int = 0


async def _sweep_rows(
    fetch: Callable[[int, int], Awaitable[tuple[list[Any], int]]],
    *,
    page_size: int,
    row_limit: int,
) -> list[Any]:
    """Read every matching row through a repository's own page method.

    The repository returns ``(page, total)``; this walks it to the end or to
    ``row_limit``, whichever comes first. ``row_limit`` is a *bound*, not a filter:
    a set larger than it is under-counted rather than invented, which is the honest
    direction to be wrong in.

    Args:
        fetch: The repository's list method, called as ``fetch(limit, offset)``.
        page_size: Rows per round trip. Matches the repository's own ceiling.
        row_limit: Hard ceiling on rows read.

    Returns:
        The rows, in the repository's own order.
    """
    rows: list[Any] = []
    offset = 0
    total: int | None = None
    while offset < row_limit:
        page, reported_total = await fetch(page_size, offset)
        total = reported_total if total is None else total
        rows.extend(page)
        if len(page) < page_size or offset + page_size >= total:
            break
        offset += page_size
    return rows


def _validated_kind(value: str) -> None:
    """Refuse a career record kind outside the closed vocabulary.

    Checked here rather than left to the repository so the caller gets a
    :class:`ValidationError` — the 422 the route documents — instead of a bare
    ``ValueError`` with no registered handler behind it.

    Raises:
        ValidationError: If ``value`` is not one of the three members. The column
            chooses the *shape* of the record rather than describing it, so an
            unrecognised value files the row under no section of the timeline it
            belongs to.
    """
    try:
        validate_career_record_kind(value)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def _validated_evidence_type(value: str) -> None:
    """Refuse a career evidence type outside the closed vocabulary.

    Raises:
        ValidationError: If ``value`` is not one of the seven members. That column is
            one of the six in ``uq_career_evidence_source_identity``, so a value
            nothing recognises does not merely fail to render — it defeats the
            constraint that keeps derived evidence from being inserted twice.
    """
    try:
        validate_career_evidence_type(value)
    except ValueError as error:
        raise ValidationError(str(error)) from error


def _plural(count: int, noun: str) -> str:
    """``"3 career records"`` / ``"1 career record"``.

    Only the count and the noun. The sentences this appears in exist to state what
    was recorded, so they take no adjective, no comparative and no "you have"
    framing that would read as a verdict about the person reading them.
    """
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


class CareerIntelligenceService:
    """Read and write one account's career profile, its records and its evidence.

    One instance per request, built from repositories rather than from a session,
    exactly like :class:`~app.services.developer.service.DeveloperIntelligenceService`
    and :class:`~app.services.learning.service.LearningIntelligenceService`. It holds
    no state between calls, so two concurrent ``PUT /career/profile`` requests are
    both correct: the unique ``user_id`` arbitrates them rather than anything this
    class remembers.

    **Nothing here writes a qualification.** Every ``title``, ``organisation``,
    ``date``, ``summary`` and ``ACHIEVEMENT`` reaches the database exactly as the
    request body spelled it. There is no generator, no import, and no future one
    planned — a schema wide enough to describe every possible qualification is a
    schema that will eventually fill one in.
    """

    def __init__(
        self,
        repositories: CareerRepository,
        *,
        learning: LearningRepository,
        projects: ProjectRepository,
        developer: DeveloperRepository,
        activity: ActivityService | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Wire the service.

        Args:
            repositories: Phase 9 career persistence. Every profile, record and
                evidence row this service reads or writes goes through it, so every
                statement is owner-scoped in one place.
            learning: Learning persistence, read for two figures only: the count of
                recorded learning activities, and the ownership check on a ``skill_id``
                an evidence row points at. Read-only here — recording an activity is
                the learning service's write, and a career edit never moves a skill.
            projects: Project persistence, for the project counts and for proving a
                ``project_id`` belongs to the caller before it is written onto an
                evidence row.
            developer: Phase 8 persistence, for the repository count and for the
                single commit aggregate behind the ``project_activity`` feature.
            activity: The history sink. ``None`` runs every lifecycle rule with
                nowhere to record them, which is a real mode for exercising the
                service against a hand-built snapshot and not a mode the API layer
                uses.
            settings: Resolved from the environment when not supplied. Supplies the
                evidence cap and the window bounds this phase reads.
        """
        self.repositories = repositories
        self.learning = learning
        self.projects = projects
        self.developer = developer
        self.activity = activity
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------
    # The profile
    # ------------------------------------------------------------------

    async def get_profile(self, *, owner: User) -> CareerProfileRead | None:
        """This account's profile, or ``None`` when it has never been written.

        ``None`` is a **cold start, not an error**, and it is deliberately not a
        ``NotFoundError``. A career record, a repository or a risk are all addressed
        by an id the caller supplied, so a miss is a 404; the profile is not addressed
        by anything, and an account that has never written one is in a state the UI
        has to render rather than an absence it has to explain. The service returns
        the honest ``None`` and never fills the gap with a generated profile — an
        empty profile invented here would be the first career row NEXUS wrote.
        """
        profile = await self.repositories.get_profile(owner.id)
        return None if profile is None else CareerProfileRead.model_validate(profile)

    async def upsert_profile(self, *, owner: User, values: Mapping[str, Any]) -> CareerProfileRead:
        """Write the one profile this account has, creating it if it is absent.

        ``PUT``, not ``POST``. The caller passes only the fields the user actually
        set — the route builds the mapping with ``exclude_unset`` — because ``None``
        means "write SQL NULL" here and would otherwise clear a headline the user
        never mentioned. Every writable column is something the user typed; there is
        no field NEXUS may fill in, and a caller reaching for one is told so by name
        rather than being quietly ignored.

        An empty mapping is refused. It would write nothing but a row, and a profile
        created by an empty body is the one way this account ends up with an empty
        profile it did not ask for.

        Args:
            owner: The account whose profile this is. It is also the upsert key, and
                it is never taken from the request body.
            values: Column name to new value, restricted to the repository's
                editable set. ``links`` is replaced wholesale; an empty list is the
                user having supplied no URLs.

        Returns:
            The stored profile, with the server-defaulted timestamps filled in.

        Raises:
            ValidationError: If ``values`` is empty or names a column the profile
                write does not own.
        """
        profile = await self._write(lambda: self.repositories.upsert_profile(owner.id, values))
        await self._record_event(
            ActivityEvent.CAREER_PROFILE_UPDATED,
            owner=owner,
            metadata={"profile_id": str(profile.id), "fields": sorted(values)},
        )
        return CareerProfileRead.model_validate(profile)

    # ------------------------------------------------------------------
    # The dated records
    # ------------------------------------------------------------------

    async def list_experience(
        self,
        *,
        owner: User,
        kind: str | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> CareerExperienceListRead:
        """One page of dated records, with the kind tally and the open count beside it.

        The two counts describe **every matching record**, not this page, so the
        header beside a page cannot understate a band that continues onto page two.
        Education, work experience and certifications share one table and are
        separated by ``kind`` alone.

        Args:
            owner: Whose records to list.
            kind: Narrow to one :class:`~app.models.enums.CareerRecordKind` member,
                or ``None`` for the whole timeline.
            limit: Page size, clamped by the repository.
            offset: Rows to skip, floored at zero.

        Returns:
            The page, its total, and the shape split across every matching record.

        Raises:
            ValidationError: If ``kind`` is outside the closed vocabulary. An
                unrecognised kind would file the row under no section of the timeline
                it belongs to.
        """
        if kind is not None:
            _validated_kind(kind)
        rows, total = await self.repositories.list_experience(
            owner.id, kind=kind, limit=limit, offset=offset
        )
        tally = await self._experience_tally(owner.id, kind=kind)
        return CareerExperienceListRead(
            items=[CareerExperienceRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=max(0, offset),
            by_kind=tally.by_kind,
            current_count=tally.current_count,
            summary=band_count_sentence(
                tally.by_kind, total, _RECORD_LIST_SUBJECT, _RECORD_KIND_ORDER
            ),
        )

    async def create_experience(
        self,
        *,
        owner: User,
        kind: str,
        title: str,
        organisation: str | None = None,
        started_on: date | None = None,
        ended_on: date | None = None,
        description: str | None = None,
        url: str | None = None,
    ) -> CareerExperienceRead:
        """Record one dated line the user supplied.

        Every value is a transcription: the ``title`` the user wrote, the
        ``organisation`` they named or left null, the dates they gave. There is no
        lookup behind any of them, and ``organisation`` being null is the normal case
        for a self-directed project rather than a gap to be filled later.

        The dates are checked **before** the write, not by letting the table refuse
        them: a range that runs backwards is a typo, and the check belongs where the
        message can be a sentence rather than a constraint name.

        Args:
            owner: Whose record this is.
            kind: One of ``education``, ``experience`` or ``certification``.
            title: The user's own name for the record.
            organisation: Who issued it or where it was done. Nullable.
            started_on: When it began, if the user said.
            ended_on: When it ended. ``None`` means current.
            description: The user's description of what it involved.
            url: A link the user supplied. Never fetched.

        Returns:
            The stored record.

        Raises:
            ValidationError: If ``kind`` is outside the vocabulary, ``title`` is
                empty, or ``ended_on`` precedes ``started_on``.
        """
        _validated_kind(kind)
        if not title.strip():
            raise ValidationError("A career record must carry the name you gave it.")
        self._assert_dates_in_order(started_on, ended_on)

        record = await self._write(
            lambda: self.repositories.create_experience(
                owner.id,
                kind=kind,
                title=title,
                organisation=organisation,
                started_on=started_on,
                ended_on=ended_on,
                description=description,
                url=url,
            ),
            integrity=ValidationError(_DATES_OUT_OF_ORDER),
        )
        return CareerExperienceRead.model_validate(record)

    async def get_experience(
        self, *, owner: User, experience_id: uuid.UUID
    ) -> CareerExperienceRead:
        """One dated record, or a 404 that does not confirm whose it was.

        Args:
            owner: The caller.
            experience_id: The record to read.

        Returns:
            The record as the wire shape.

        Raises:
            NotFoundError: If no record with that id belongs to this account —
                word for word the same answer an id nobody issued gives.
        """
        record = await self.repositories.get_experience(owner.id, experience_id)
        if record is None:
            raise NotFoundError(_EXPERIENCE_NOT_FOUND)
        return CareerExperienceRead.model_validate(record)

    async def update_experience(
        self, *, owner: User, experience_id: uuid.UUID, values: Mapping[str, Any]
    ) -> CareerExperienceRead:
        """Correct one dated record. A correction the user made, not a re-filing.

        ``kind`` is editable because the repository says it is, and ``created_at`` is
        absent from the write set entirely. The caller's mapping is applied with
        ``exclude_unset`` at the route: an omitted key leaves the column alone and an
        explicit null clears it, which is how a self-directed entry stays honest.

        The date check runs against the **merged** record rather than against the
        patch. A patch that only supplies ``ended_on`` would sail past a check on its
        own values and land a range that runs backwards against the stored start
        date.

        Args:
            owner: The caller.
            experience_id: The record to edit.
            values: Column name to new value, restricted to the editable set.

        Returns:
            The updated record.

        Raises:
            ValidationError: If the mapping is empty, names an unknown column, or
                would leave ``ended_on`` before ``started_on``.
            NotFoundError: If the record is not this account's.
        """
        record = await self.repositories.get_experience(owner.id, experience_id)
        if record is None:
            raise NotFoundError(_EXPERIENCE_NOT_FOUND)
        self._assert_dates_in_order(
            values.get("started_on", record.started_on),
            values.get("ended_on", record.ended_on),
        )
        updated = await self._write(
            lambda: self.repositories.update_experience(owner.id, experience_id, values),
            integrity=ValidationError(_DATES_OUT_OF_ORDER),
        )
        if updated is None:
            raise NotFoundError(_EXPERIENCE_NOT_FOUND)
        return CareerExperienceRead.model_validate(updated)

    async def delete_experience(self, *, owner: User, experience_id: uuid.UUID) -> None:
        """Remove one dated record.

        A plain owner-scoped delete, with no read of the evidence rows that may name
        this record: those foreign keys are ``SET NULL``, so a claim the user made
        about the record survives as their own claim with the pointer dropped. A
        delete that swept the children would be a delete that believed the evidence
        was a view rather than a trail.

        Args:
            owner: The caller.
            experience_id: The record to delete.

        Raises:
            NotFoundError: If no record with that id belongs to this account.
        """
        if not await self.repositories.delete_experience(owner.id, experience_id):
            raise NotFoundError(_EXPERIENCE_NOT_FOUND)

    # ------------------------------------------------------------------
    # The evidence
    # ------------------------------------------------------------------

    async def list_evidence(
        self,
        *,
        owner: User,
        evidence_type: str | None = None,
        project_id: uuid.UUID | None = None,
        skill_id: uuid.UUID | None = None,
        repository_id: uuid.UUID | None = None,
        since: date | None = None,
        until: date | None = None,
        limit: int = _DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> CareerEvidenceListRead:
        """One page of evidence, newest first, with the tallies beside it.

        ``by_type``, ``by_source`` and ``manual_count`` describe every matching row
        rather than this page. ``by_type`` is completed against the closed
        vocabulary by the schema, so a client reading ``by_type.certification`` never
        meets a missing key; ``by_source`` is left as counted, because that
        vocabulary is open and a subsystem that has never run has no band to
        zero-fill.

        Args:
            owner: Whose evidence to list.
            evidence_type: Narrow to one
                :class:`~app.models.enums.CareerEvidenceType` member.
            project_id: Narrow to the evidence drawn from one project. Evidence with
                no project is excluded rather than folded into it.
            skill_id: Narrow to the evidence drawn from one skill.
            repository_id: Narrow to the evidence drawn from one repository.
            since: Inclusive lower bound on ``occurred_on``.
            until: Exclusive upper bound.
            limit: Page size, clamped by the repository.
            offset: Rows to skip.

        Returns:
            The page, its total, and the tallies across every matching row.

        Raises:
            ValidationError: If ``evidence_type`` is outside the closed vocabulary.
                That column is one of the six in the deduplication key, so a value
                nothing recognises does not merely fail to render.
        """
        if evidence_type is not None:
            _validated_evidence_type(evidence_type)
        rows, total = await self.repositories.list_evidence(
            owner.id,
            evidence_type=evidence_type,
            project_id=project_id,
            skill_id=skill_id,
            repository_id=repository_id,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
        tally = await self._evidence_tally(
            owner.id,
            evidence_type=evidence_type,
            project_id=project_id,
            skill_id=skill_id,
            repository_id=repository_id,
            since=since,
            until=until,
        )
        return CareerEvidenceListRead(
            items=[CareerEvidenceRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=max(0, offset),
            by_type=tally.by_type,
            by_source=tally.by_source,
            manual_count=tally.manual_count,
            summary=band_count_sentence(
                tally.by_type, total, _EVIDENCE_LIST_SUBJECT, _EVIDENCE_TYPE_ORDER
            ),
        )

    async def create_evidence(
        self,
        *,
        owner: User,
        evidence_type: str,
        title: str,
        occurred_on: date | None = None,
        description: str | None = None,
        project_id: uuid.UUID | None = None,
        skill_id: uuid.UUID | None = None,
        repository_id: uuid.UUID | None = None,
        source: str = DEFAULT_CAREER_EVIDENCE_SOURCE,
    ) -> CareerEvidenceRead:
        """Record one thing the user wants to be able to point at.

        Three checks happen before the write, in this order:

        1. **Ownership of what it points at.** A ``project_id``, ``skill_id`` or
           ``repository_id`` belonging to another account is *not found*, not
           forbidden — a 403 would confirm the id exists. All three may be omitted,
           which is what a hand-written achievement looks like.
        2. **The cap.** ``career_max_evidence`` is checked by counting rows before
           the insert, so the limit is answerable rather than discovered afterwards.
        3. **Duplication.** :meth:`CareerRepository.find_duplicate_evidence` is asked
           first, and a hit becomes a ``ConflictError`` carrying a sentence a person
           can act on. An :exc:`~sqlalchemy.exc.IntegrityError` carries a constraint
           name instead. The check reproduces the constraint's own treatment of
           nulls — several manual rows coexist, a derived one cannot be inserted
           twice — and the constraint itself stays as the race net underneath.

        ``occurred_on`` is required. A dated record is not the only thing a user may
        be entering, but evidence without a day cannot be placed in a timeline, and
        the placeholder that would make it renderable would be a fabricated date.

        Args:
            owner: Whose evidence this is.
            evidence_type: One of the seven
                :class:`~app.models.enums.CareerEvidenceType` members.
                ``repository_activity`` means commits were observed — not that a task
                was completed, and not that anything was finished.
            title: The user's own line for it.
            occurred_on: The day it happened.
            description: An optional note in the user's words.
            project_id: The project it was drawn from, if any.
            skill_id: The skill it was drawn from, if any.
            repository_id: The repository it was drawn from, if any.
            source: The subsystem that derived it, or ``manual``. An open
                vocabulary, and part of the uniqueness key — so a typo here is the one
                mistake on this table that would quietly allow a duplicate, which is
                why the default is the module's single spelling of it.

        Returns:
            The stored evidence row.

        Raises:
            ValidationError: If ``evidence_type`` is outside the vocabulary,
                ``title`` is empty, or ``occurred_on`` is missing.
            NotFoundError: If a supplied ``project_id``, ``skill_id`` or
                ``repository_id`` belongs to another account.
            ConflictError: If the account is at the evidence cap, or a row with the
                same identity already exists.
        """
        _validated_evidence_type(evidence_type)
        if not title.strip():
            raise ValidationError("Career evidence must carry the line you wrote for it.")
        if occurred_on is None:
            raise ValidationError(_UNDATED_EVIDENCE)
        await self._assert_linked_records(
            owner=owner, project_id=project_id, skill_id=skill_id, repository_id=repository_id
        )
        if await self.repositories.count_evidence(owner.id) >= self.settings.career_max_evidence:
            raise ConflictError(
                f"This account already has the maximum of "
                f"{self.settings.career_max_evidence} career evidence records."
            )
        duplicate = await self.repositories.find_duplicate_evidence(
            owner.id,
            evidence_type=evidence_type,
            source=source,
            project_id=project_id,
            skill_id=skill_id,
            repository_id=repository_id,
        )
        if duplicate is not None:
            raise ConflictError(_DUPLICATE_EVIDENCE)

        evidence = await self._write(
            lambda: self.repositories.create_evidence(
                owner.id,
                evidence_type=evidence_type,
                title=title,
                occurred_on=occurred_on,
                description=description,
                project_id=project_id,
                skill_id=skill_id,
                repository_id=repository_id,
                source=source,
            ),
            integrity=ConflictError(_DUPLICATE_EVIDENCE),
        )
        await self._record_event(
            ActivityEvent.CAREER_EVIDENCE_ADDED,
            owner=owner,
            project_id=evidence.project_id,
            metadata={
                "evidence_id": str(evidence.id),
                "evidence_type": str(evidence.evidence_type),
                "source": str(evidence.source),
            },
        )
        return CareerEvidenceRead.model_validate(evidence)

    async def get_evidence(self, *, owner: User, evidence_id: uuid.UUID) -> CareerEvidenceRead:
        """One evidence row, or a 404 that does not confirm whose it was.

        Args:
            owner: The caller.
            evidence_id: The evidence to read.

        Returns:
            The evidence row as the wire shape.

        Raises:
            NotFoundError: If no row with that id belongs to this account.
        """
        evidence = await self.repositories.get_evidence(owner.id, evidence_id)
        if evidence is None:
            raise NotFoundError(_EVIDENCE_NOT_FOUND)
        return CareerEvidenceRead.model_validate(evidence)

    async def update_evidence(
        self, *, owner: User, evidence_id: uuid.UUID, values: Mapping[str, Any]
    ) -> CareerEvidenceRead:
        """Correct one evidence row — the words and the date, never the provenance.

        The wire model for this edit carries no ``source`` and no ``*_id``, because
        those are the row's identity: re-pointing it at a different project would let
        a rename become a second record, which is the exact failure
        ``uq_career_evidence_source_identity`` exists to prevent. The repository
        still permits the edit for a Phase 10 subsystem that legitimately needs it,
        so this service re-runs the duplicate check against the **resulting**
        identity when one is supplied, and ignores the row's own identity when that
        is what the check returns.

        An explicit ``occurred_on: null`` is refused. Undated evidence cannot be
        placed in a timeline, and the placeholder that would make it renderable would
        be a date the user never gave.

        Args:
            owner: The caller.
            evidence_id: The evidence to edit.
            values: Column name to new value, restricted to the editable set.

        Returns:
            The updated evidence row.

        Raises:
            ValidationError: If the mapping is empty, names an unknown column, carries
                an unknown ``evidence_type``, or clears ``occurred_on``.
            NotFoundError: If the row is not this account's.
            ConflictError: If the edit would move the row onto an identity another row
                already holds.
        """
        evidence = await self.repositories.get_evidence(owner.id, evidence_id)
        if evidence is None:
            raise NotFoundError(_EVIDENCE_NOT_FOUND)
        if values.get("occurred_on", evidence.occurred_on) is None:
            raise ValidationError(_UNDATED_EVIDENCE)
        if "evidence_type" in values:
            _validated_evidence_type(values["evidence_type"])

        identity_columns = ("source", "project_id", "skill_id", "repository_id")
        if any(column in values for column in identity_columns):
            await self._assert_linked_records(
                owner=owner,
                project_id=values.get("project_id", evidence.project_id),
                skill_id=values.get("skill_id", evidence.skill_id),
                repository_id=values.get("repository_id", evidence.repository_id),
            )
            duplicate = await self.repositories.find_duplicate_evidence(
                owner.id,
                evidence_type=values.get("evidence_type", evidence.evidence_type),
                source=values.get("source", evidence.source),
                project_id=values.get("project_id", evidence.project_id),
                skill_id=values.get("skill_id", evidence.skill_id),
                repository_id=values.get("repository_id", evidence.repository_id),
            )
            if duplicate is not None and duplicate.id != evidence_id:
                raise ConflictError(_DUPLICATE_EVIDENCE)

        updated = await self._write(
            lambda: self.repositories.update_evidence(owner.id, evidence_id, values),
            integrity=ConflictError(_DUPLICATE_EVIDENCE),
        )
        if updated is None:
            raise NotFoundError(_EVIDENCE_NOT_FOUND)
        await self._record_event(
            ActivityEvent.CAREER_EVIDENCE_UPDATED,
            owner=owner,
            project_id=updated.project_id,
            metadata={"evidence_id": str(updated.id), "fields": sorted(values)},
        )
        return CareerEvidenceRead.model_validate(updated)

    async def delete_evidence(self, *, owner: User, evidence_id: uuid.UUID) -> None:
        """Remove one evidence row.

        The claim itself, or nothing. Another account's id deletes nothing and
        reports the same ``False`` an unknown id does.

        Args:
            owner: The caller.
            evidence_id: The evidence to delete.

        Raises:
            NotFoundError: If no row with that id belongs to this account.
        """
        if not await self.repositories.delete_evidence(owner.id, evidence_id):
            raise NotFoundError(_EVIDENCE_NOT_FOUND)

    # ------------------------------------------------------------------
    # The summary and the features
    # ------------------------------------------------------------------

    async def summary(self, *, owner: User, window_days: int | None = None) -> CareerSummaryRead:
        """The career page's headline counts, and one factual sentence about them.

        Counts only, over a window whose length is carried beside them so the
        sentence underneath can be true. **There is no score, no rank, no "profile
        strength" and no ordering of evidence by importance** — anything that
        ordered a person's evidence by weight would be a judgement about them that no
        column in this schema could justify.

        ``has_data`` is the cold-start flag. On an account with no profile, no records
        and no evidence it is ``False``, so a page of zeroes reads as an absence
        rather than as a finding about the person who owns the account — and the
        sentence says so in words rather than leaving a reader to infer it from six
        zeroes.

        Args:
            owner: The caller.
            window_days: Length of the window. Defaults to
                ``learning_default_window_days`` and is rejected above
                ``learning_max_window_days``.

        Returns:
            The summary.

        Raises:
            ValidationError: If the window is longer than the configured ceiling.
                Silently shortening it would make the returned ``window_days``
                disagree with what the caller asked for.
        """
        days, window_start, window_end = await self._resolve_window(window_days)
        profile = await self.repositories.get_profile(owner.id)
        _page, record_count = await self.repositories.list_experience(owner.id, limit=1)
        evidence_count = await self.repositories.count_evidence(owner.id)
        _windowed, evidence_in_window = await self.repositories.list_evidence(
            owner.id,
            # ``occurred_on`` is a date, so the window's own instants are rounded to
            # the days they cover and the upper bound is made exclusive on the day
            # after. A timestamp comparison would quietly exclude evidence dated on
            # the window's final day.
            since=window_start.date(),
            until=window_end.date() + timedelta(days=1),
            limit=1,
        )
        tally = await self._evidence_tally(owner.id)
        project_stats = await self.projects.stats_for_user(owner.id)
        learning_totals = await self.learning.activity_totals(owner.id)

        completed_projects = int(project_stats.get(ProjectStatus.COMPLETED.value, 0))
        project_count = int(project_stats.get("total", 0))
        has_data = profile is not None or record_count > 0 or evidence_count > 0
        return CareerSummaryRead(
            has_profile=profile is not None,
            target_role=profile.target_role if profile is not None else None,
            link_count=len(profile.links) if profile is not None else 0,
            record_count=record_count,
            evidence_count=evidence_count,
            evidence_in_window=evidence_in_window,
            manual_evidence_count=tally.manual_count,
            linked_project_count=tally.linked_project_count,
            project_count=project_count,
            completed_project_count=completed_projects,
            repository_count=await self.developer.count_repositories(owner.id),
            skills_with_evidence=tally.skills_with_evidence,
            learning_activity_count=learning_totals.activities,
            window_days=days,
            window_start=window_start,
            window_end=window_end,
            latest_evidence_on=tally.latest_evidence_on,
            has_data=has_data,
            summary=_summary_sentence(
                has_profile=profile is not None,
                record_count=record_count,
                evidence_count=evidence_count,
                manual_evidence_count=tally.manual_count,
                evidence_in_window=evidence_in_window,
                window_days=days,
            ),
        )

    async def features(
        self, *, owner: User, window_days: int | None = None
    ) -> CareerFeatureVectorRead:
        """The ML-ready feature vector: six named numbers and their meanings.

        **An extractor, not a model.** The version string is the contract with
        whatever trains on it later — a v2 must not be able to typecheck against v1
        column meanings. There is no model, no inference and no registry behind this
        shape; Phase 10 does that, and this phase does not.

        ``project_activity`` is the contract's own worked example of the null-not-zero
        rule. It is ``None`` when **no repository has ever been scanned**, because a
        zero there asserts that a repository exists and carries no commits when the
        truth is that nobody has looked — and inside a training matrix a fabricated
        zero is indistinguishable from an observed one. It is equally ``None`` when
        the account has no completed project, because the ratio has no denominator
        and ``0/0`` is not a number. The five figures beside it are counts of the
        user's own rows and of the account's records, so a genuine zero survives.

        The commits in the numerator are read with a single join across commits,
        repositories and projects, so only commits belonging to repositories linked
        to one of *this* account's completed projects are counted and only from
        repositories that have been scanned at least once. It is the one statement in
        this module that is not delegated: it spans three tables, and asking three
        repositories one round trip each would make the cost of a career page grow
        with the number of repositories rather than with its page size.

        Args:
            owner: The caller.
            window_days: The window ``project_activity`` is computed over. Defaults
                to ``learning_default_window_days``.

        Returns:
            The vector, stamped ``career_features.v1`` and the database clock's
            ``generated_at``.

        Raises:
            ValidationError: If the window exceeds the configured ceiling.
        """
        days, window_start, window_end = await self._resolve_window(window_days)
        project_stats = await self.projects.stats_for_user(owner.id)
        projects_completed = int(project_stats.get(ProjectStatus.COMPLETED.value, 0))
        commits, scanned_repositories = await self._completed_project_commits(
            owner.id, window_start=window_start, window_end=window_end
        )
        profile = await self.repositories.get_profile(owner.id)
        tally = await self._evidence_tally(owner.id)
        learning_totals = await self.learning.activity_totals(owner.id)

        project_activity: float | None = None
        if projects_completed > 0 and scanned_repositories > 0:
            project_activity = round(commits / projects_completed, 4)

        return CareerFeatureVectorRead(
            schema_version=CAREER_FEATURE_SCHEMA_VERSION,
            generated_at=window_end,
            window_days=days,
            features=CareerFeatureValues(
                projects_completed=projects_completed,
                project_activity=project_activity,
                repositories=await self.developer.count_repositories(owner.id),
                relevant_skill_evidence=tally.skill_linked_count,
                learning_activity=learning_totals.activities,
                portfolio_evidence_count=tally.manual_count
                + (len(profile.links) if profile is not None else 0),
            ),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _assert_linked_records(
        self,
        *,
        owner: User,
        project_id: uuid.UUID | None,
        skill_id: uuid.UUID | None,
        repository_id: uuid.UUID | None,
    ) -> None:
        """Prove every record an evidence row points at belongs to the caller.

        Each id is user input, and each is a pointer onto somebody else's row unless
        it is resolved through an owner-scoped lookup before the write. A miss raises
        the same not-found every other foreign id raises: a 403 would confirm the id
        exists, and the career page is not a directory of other people's projects.

        Raises:
            NotFoundError: If any supplied id does not belong to this account.
        """
        if project_id is not None and (
            await self.projects.get_by_id_for_user(project_id, owner.id) is None
        ):
            raise NotFoundError(_PROJECT_NOT_FOUND)
        if skill_id is not None and await self.learning.get_skill(owner.id, skill_id) is None:
            raise NotFoundError(_SKILL_NOT_FOUND)
        if repository_id is not None and (
            await self.developer.get_repository(owner.id, repository_id) is None
        ):
            raise NotFoundError(_REPOSITORY_NOT_FOUND)

    async def _evidence_tally(
        self,
        owner_id: uuid.UUID,
        *,
        evidence_type: str | None = None,
        project_id: uuid.UUID | None = None,
        skill_id: uuid.UUID | None = None,
        repository_id: uuid.UUID | None = None,
        since: date | None = None,
        until: date | None = None,
    ) -> _EvidenceTally:
        """Whole-set counts over the matching evidence rows.

        Read through :meth:`CareerRepository.list_evidence` — the same method, with
        the same filters and the same owner predicate, that produced the page these
        counts sit beside. A second copy of the filter construction in this module
        would be a second answer that can disagree with the list above it, which is
        the failure the list schemas warn about by name.

        Args:
            owner_id: Whose evidence to count.
            evidence_type: Narrow to one evidence type.
            project_id: Narrow to the evidence drawn from one project.
            skill_id: Narrow to the evidence drawn from one skill.
            repository_id: Narrow to the evidence drawn from one repository.
            since: Inclusive lower bound on ``occurred_on``.
            until: Exclusive upper bound.

        Returns:
            The tally. Every field is zero or ``None`` when nothing matched, which is
            a real answer about a set that was searched and found empty.
        """
        rows = await _sweep_rows(
            lambda limit, offset: self.repositories.list_evidence(
                owner_id,
                evidence_type=evidence_type,
                project_id=project_id,
                skill_id=skill_id,
                repository_id=repository_id,
                since=since,
                until=until,
                limit=limit,
                offset=offset,
            ),
            page_size=_SWEEP_PAGE_SIZE,
            row_limit=max(1, self.settings.career_max_evidence),
        )
        by_type: dict[str, int] = {}
        by_source: dict[str, int] = {}
        skills: set[uuid.UUID] = set()
        projects: set[uuid.UUID] = set()
        manual = 0
        linked = 0
        latest: date | None = None
        for row in rows:
            by_type[str(row.evidence_type)] = by_type.get(str(row.evidence_type), 0) + 1
            by_source[str(row.source)] = by_source.get(str(row.source), 0) + 1
            if row.source == DEFAULT_CAREER_EVIDENCE_SOURCE:
                manual += 1
            if row.skill_id is not None:
                skills.add(row.skill_id)
                linked += 1
            if row.project_id is not None:
                projects.add(row.project_id)
            if latest is None or row.occurred_on > latest:
                latest = row.occurred_on
        return _EvidenceTally(
            by_type=by_type,
            by_source=by_source,
            manual_count=manual,
            skill_linked_count=linked,
            skills_with_evidence=len(skills),
            linked_project_count=len(projects),
            latest_evidence_on=latest,
        )

    async def _experience_tally(
        self, owner_id: uuid.UUID, *, kind: str | None = None
    ) -> _TimelineTally:
        """Whole-set counts over the matching dated records.

        Read through :meth:`CareerRepository.list_experience` for the same reason as
        :meth:`_evidence_tally`. Bounded by :data:`_TIMELINE_SWEEP_ROW_LIMIT`: past
        it the shape counts under-state rather than guess.

        Args:
            owner_id: Whose records to count.
            kind: Narrow to one record kind.

        Returns:
            The tally, zeroed when nothing matched.
        """
        rows = await _sweep_rows(
            lambda limit, offset: self.repositories.list_experience(
                owner_id, kind=kind, limit=limit, offset=offset
            ),
            page_size=_SWEEP_PAGE_SIZE,
            row_limit=_TIMELINE_SWEEP_ROW_LIMIT,
        )
        by_kind: dict[str, int] = {}
        current = 0
        for row in rows:
            by_kind[str(row.kind)] = by_kind.get(str(row.kind), 0) + 1
            if row.ended_on is None:
                current += 1
        return _TimelineTally(by_kind=by_kind, current_count=current)

    async def _completed_project_commits(
        self, owner_id: uuid.UUID, *, window_start: datetime, window_end: datetime
    ) -> tuple[int, int]:
        """``(commits, scanned repositories)`` behind ``project_activity``.

        One join across commits, repositories and projects rather than one query per
        repository: the career page renders once and the per-repository cost would
        make a dashboard grow with the number of registered repositories.

        Two predicates do the honesty work. The owner is asserted on **both** tables
        the join touches, and ``last_scanned_at IS NOT NULL`` restricts the numerator
        to repositories somebody actually read — which is what makes the caller's
        decision to report ``None`` rather than ``0.0`` on a zero here something the
        data supports rather than a guess.

        Args:
            owner_id: Whose history to read.
            window_start: Inclusive lower bound on ``committed_at``.
            window_end: Exclusive upper bound.

        Returns:
            The number of recorded commits inside the window on repositories linked
            to one of the owner's completed projects, and how many distinct such
            repositories have been scanned at least once. A zero second figure is the
            "never scanned" answer the feature turns into ``None``.
        """
        statement = (
            select(
                func.coalesce(func.count(GitCommit.id), 0),
                func.count(func.distinct(GitRepository.id)),
            )
            .select_from(GitCommit)
            .join(GitRepository, GitRepository.id == GitCommit.repository_id)
            .join(Project, Project.id == GitRepository.project_id)
            .where(
                GitCommit.user_id == owner_id,
                GitRepository.user_id == owner_id,
                Project.owner_id == owner_id,
                Project.status == ProjectStatus.COMPLETED.value,
                GitRepository.last_scanned_at.is_not(None),
                GitCommit.committed_at >= window_start,
                GitCommit.committed_at < window_end,
            )
        )
        row = (await self.developer.session.execute(statement)).one()
        return int(row[0]), int(row[1])

    async def _write(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        integrity: Exception | None = None,
    ) -> Any:
        """Run one repository write, translating its failure vocabulary into ours.

        Two translations, both of them the difference between a readable answer and a
        stack trace:

        * a ``ValueError`` from the repository's own validation — an unknown column,
          an empty mapping, a vocabulary the enum does not have — becomes
          :class:`ValidationError`, the 422 the route documents. A bare
          ``ValueError`` has no registered handler and would reach the client as an
          unhandled 500;
        * an :exc:`~sqlalchemy.exc.IntegrityError` becomes whatever the caller says
          this table can actually refuse. Every table reachable through here has its
          remaining constraints checked before the write, so the one that can still
          fire is the race the service cannot see coming — and a constraint name is
          not a sentence.

        The session is rolled back before the exception is raised. Without that the
        transaction stays aborted and the next statement on the same connection fails
        with an error about the *previous* one, which is how one conflict turns into
        a page of unrelated errors.

        Args:
            operation: The repository call to run.
            integrity: The exception an ``IntegrityError`` should become, or ``None``
                to let it propagate — which nothing in this module asks for.

        Returns:
            Whatever the repository returned.

        Raises:
            ValidationError: If the repository refused the write on value grounds.
            Exception: ``integrity`` itself, if the write lost a race.
        """
        try:
            return await operation()
        except ValueError as error:
            await self.repositories.session.rollback()
            raise ValidationError(str(error)) from error
        except IntegrityError as error:
            await self.repositories.session.rollback()
            if integrity is None:
                raise
            raise integrity from error

    @staticmethod
    def _assert_dates_in_order(started_on: date | None, ended_on: date | None) -> None:
        """Refuse a range that ends before it starts.

        Checked here rather than left to ``ck_career_experience_dates_in_order`` so
        the caller gets a sentence instead of a constraint name, and checked against
        the *merged* record on a patch so that supplying only ``ended_on`` cannot
        land a backwards range against a stored start date.

        Single-date records are untouched: ``ended_on IS NULL`` is how a current role
        says so, and a start nobody knows is not a claim to check.

        Raises:
            ValidationError: If both dates are present and the end precedes the start.
        """
        if started_on is not None and ended_on is not None and ended_on < started_on:
            raise ValidationError(_DATES_OUT_OF_ORDER)

    def _window_days(self, window_days: int | None) -> int:
        """Resolve a requested window length, refusing one above the ceiling.

        Rejected rather than clamped: silently shortening it would make the
        ``window_days`` on the response disagree with what the caller asked for,
        which is the kind of quiet disagreement this codebase treats as a bug.

        The bounds come from the learning settings because the frozen configuration
        defines no career-specific window — the two pages share one window vocabulary
        rather than declaring two ceilings for the same question.

        Raises:
            ValidationError: If the window exceeds ``learning_max_window_days``.
        """
        requested = (
            self.settings.learning_default_window_days if window_days is None else int(window_days)
        )
        ceiling = max(1, self.settings.learning_max_window_days)
        if requested > ceiling:
            raise ValidationError(f"A window may span at most {ceiling} days.")
        return max(1, requested)

    async def _resolve_window(self, window_days: int | None) -> tuple[int, datetime, datetime]:
        """``(days, inclusive start, exclusive end)`` anchored on the DB clock.

        Half-open at the top so a row landing exactly on the boundary belongs to one
        window rather than two, and anchored on ``now()`` rather than on the host
        clock so the figure sits on the same timeline as the rows it reads.
        """
        days = self._window_days(window_days)
        end = await self._now()
        return days, end - timedelta(days=days), end

    async def _now(self) -> datetime:
        """The database clock.

        Never ``datetime.now()``: a host whose clock drifts from the server's would
        file evidence on the wrong day, and the day a thing happened is exactly what
        the windowed counts above measure.
        """
        value = await self.repositories.session.scalar(select(func.now()))
        return _as_utc(value) if isinstance(value, datetime) else datetime.now(UTC)

    async def _record_event(
        self,
        event: ActivityEvent,
        *,
        owner: User,
        project_id: uuid.UUID | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one history row, or return immediately when there is no sink.

        Metadata is ids, counts and field names only. A title, a headline or an
        organisation is the user's own words and belongs on the row the event points
        at; duplicating it into a feed nobody asked for is how a summary view ends up
        quoting stale text about a person.
        """
        if self.activity is None:
            return
        await self.activity.record(
            event.value, user_id=owner.id, project_id=project_id, metadata=metadata
        )


def _summary_sentence(
    *,
    has_profile: bool,
    record_count: int,
    evidence_count: int,
    manual_evidence_count: int,
    evidence_in_window: int,
    window_days: int,
) -> str:
    """One factual sentence describing the counts on the career page.

    Three registers, chosen by what is actually true rather than by what would read
    best: nothing written at all, a timeline with no evidence behind it, and evidence
    that exists. Each sentence carries its own figures, and **none of them claims
    anything about the person** — not readiness, not strength, not prospects. A count
    of records is a count of records, and the sentence is the one place a summary
    decides how that count is framed.

    Args:
        has_profile: Whether the account has a profile row.
        record_count: Dated records on file.
        evidence_count: Evidence rows across the whole history.
        manual_evidence_count: Of those, the ones the user entered by hand.
        evidence_in_window: Evidence dated inside the window.
        window_days: The window's length.

    Returns:
        A sentence such as ``"No career profile has been written yet, so there is
        nothing to summarise."``
    """
    if not has_profile and record_count == 0 and evidence_count == 0:
        return (
            "No career profile has been written and no career records or evidence have "
            "been added yet, so there is nothing to summarise."
        )
    if evidence_count == 0:
        return (
            f"{_plural(record_count, 'career record')} on file and no career evidence "
            "entered yet; the page is showing only what has been written."
        )
    windowed = (
        f", of which {evidence_in_window} {'is' if evidence_in_window == 1 else 'are'} dated "
        f"in the last {window_days} days"
        if evidence_in_window
        else ""
    )
    return (
        f"{_plural(evidence_count, 'career evidence record')} recorded, "
        f"{manual_evidence_count} of them entered by hand{windowed}, alongside "
        f"{_plural(record_count, 'career record')}."
    )
