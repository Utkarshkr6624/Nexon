"""The Phase 9 career service end to end: what it writes, what it refuses, what it hides.

:meth:`app.services.career.service.CareerIntelligenceService` is the only seam
between "the user typed their career into a form" and "here is a career page".
Everything this phase is actually about is decided there and nowhere else, so this
file drives it directly rather than through HTTP — the route layer has its own file,
and a test that went through it would be testing the router as much as the service.

What the file is for
--------------------
``app/repositories/career.py`` and the migrations own storage and have their own
tests. Neither can decide the things below, because each is a statement about
*ownership*, *validation* and *honesty*:

**One profile per account, ever.** ``career_profiles.user_id`` is unique, and the
service writes it through a get-or-create rather than a blind insert. The claim under
test is that the *second* ``PUT`` revises the first row instead of failing on the
constraint or, worse, adding a second one — and it is asserted by counting rows, not
by comparing the two returned shapes.

**Another account is 404, never 403.** Every entry point is exercised: the profile
read, the record detail read, the record edit, the record delete, the evidence detail
read, the evidence edit and the evidence delete. Each raises the *same message* an id
nobody ever issued raises, because a different message would make the endpoint an
oracle for which career ids are real.

**A duplicate evidence row is a conflict in words, not a stack trace.** The unique
constraint would refuse it anyway; what the service owes is a sentence a person can
act on *before* the write. The flip side is asserted too: a second manual achievement
with all three foreign keys null is **allowed**, because nulls do not collide in a
btree unique index and refusing it would make the service contradict its own schema.

**A range that ends before it starts is a validation error, checked before the write.**
And on a patch it is checked against the *merged* record, not against the patch alone
— a patch that supplies only ``ended_on`` would otherwise land a backwards range
against the stored start date.

**An account with no career at all reads as an absence.** ``get_profile`` returns
``None`` and the summary reports ``has_data=False`` with a sentence saying there is
nothing to summarise. No profile is invented, because an invented profile would be the
first career row NEXUS wrote.

**A figure that could not be computed is ``null``, never ``0``.** The feature
vector's ``project_activity`` is the contract's own worked example: an account whose
repositories have never been scanned gets ``None``, because ``0`` would assert that a
repository exists and carries no commits when the truth is that nobody has looked.

House style, deliberately
-------------------------
Follows ``tests/test_developer_service.py``:

* ``pytestmark = pytest.mark.integration`` — every test here needs the live
  PostgreSQL the suite truncates between tests.
* Services are hand-wired in a module-level ``_service()`` helper that mirrors the way
  ``app.api.deps`` will wire them. The activity sink is always the real one: passing
  ``activity=None`` would make every event assertion pass vacuously.
* Rows are read back through **explicit column tuples**, never ORM entities. This
  session is also the one that wrote them, so an entity read would hand back whatever
  the identity map cached and a duplicate-row assertion would compare stale objects
  to fresh ones and pass for the wrong reason.
* The clock is read from the database through ``_db_now``, never from
  ``date.today()``, because the service resolves every window from ``func.now()`` and
  the two must describe the same day.

Every expected figure in this file is derived in the test's own docstring rather than
recorded from a run, and the fixtures are pinned to whole days so the arithmetic holds
at any hour.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.activity import ActivityLog
from app.models.career import CareerEvidence, CareerExperience, CareerProfile
from app.models.enums import ActivityEvent, CareerEvidenceType, GitScanStatus, ProjectStatus
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.repositories.career import CareerRepository
from app.repositories.developer import DeveloperRepository
from app.repositories.learning import LearningRepository
from app.repositories.project import ProjectRepository
from app.services.activity_service import ActivityService
from app.services.career import CareerIntelligenceService
from tests.analytics_fixtures import AnalyticsSeed, register_user

pytestmark = pytest.mark.integration

#: The window the summary and the feature vector are computed over. Thirty days is
#: the configured default, so the arithmetic below stays whole.
WINDOW = 30


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _service(
    session: AsyncSession, *, settings: Settings | None = None
) -> CareerIntelligenceService:
    """A career service wired the way ``app.api.deps`` will wire it.

    Every collaborator is the real one. The activity sink in particular: it is what
    ``CAREER_PROFILE_UPDATED``, ``CAREER_EVIDENCE_ADDED`` and
    ``CAREER_EVIDENCE_UPDATED`` are written through, and this file asserts on all
    three, so a ``None`` sink would make a third of it vacuous.

    Args:
        session: The test session. Every repository is built on the same one, which
            is why the reads below go through explicit columns.
        settings: Supplied only by the cap test, which needs a deployment whose
            ``career_max_evidence`` is one rather than the configured five hundred.
    """
    return CareerIntelligenceService(
        CareerRepository(session),
        learning=LearningRepository(session),
        projects=ProjectRepository(session),
        developer=DeveloperRepository(session),
        activity=ActivityService(ActivityRepository(session)),
        settings=settings,
    )


async def _owner(session: AsyncSession, username: str = "ada") -> User:
    """One account, inserted directly.

    Direct rather than through the API because these tests drive the service, not a
    route, and ``register_user`` writes no activity events — which matters, because
    the event assertions below count event types and a registration row would move
    them.
    """
    return await register_user(session, username=username)


async def _db_now(session: AsyncSession) -> datetime:
    """The database's clock, as an aware UTC instant.

    The same read :meth:`CareerIntelligenceService._now` performs. Evidence is
    seeded relative to this rather than to ``datetime.now()`` so that a row the
    service will count sits inside the window the service will compute.
    """
    value = await session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _registered_repository_id(
    session: AsyncSession, owner_id: uuid.UUID, tmp_path: Path, name: str
) -> uuid.UUID:
    """Register a work tree that has **never been scanned**, and return its id.

    Written through the repository rather than through the developer service because
    no git fixture is needed here: what the career page asks is whether the account
    has a registered repository at all, and what the feature vector asks is whether
    one was ever *read*. ``last_scanned_at`` stays null, which is how "registered but
    never scanned" is stored.

    Args:
        session: The test session.
        owner_id: Whose repository it is.
        tmp_path: The directory the path is built under. No directory is created —
            registering a path observes nothing, so there is nothing to observe.
        name: The repository's label.
    """
    stored = await DeveloperRepository(session).create_repository(
        owner_id, name=name, local_path=str(tmp_path / name)
    )
    assert stored.last_scanned_at is None, "the fixture must be a repository nobody has read"
    return stored.id


# ---------------------------------------------------------------------------
# Reading stored rows back
# ---------------------------------------------------------------------------

#: The columns every stored profile is read back through. Listed in full so a column
#: added to the model shows up here rather than as a silently unasserted field.
_PROFILE_COLUMNS = (
    CareerProfile.id,
    CareerProfile.user_id,
    CareerProfile.target_role,
    CareerProfile.target_domain,
    CareerProfile.headline,
    CareerProfile.summary,
    CareerProfile.location,
    CareerProfile.links,
    CareerProfile.created_at,
    CareerProfile.updated_at,
)

_EXPERIENCE_COLUMNS = (
    CareerExperience.id,
    CareerExperience.user_id,
    CareerExperience.kind,
    CareerExperience.title,
    CareerExperience.organisation,
    CareerExperience.started_on,
    CareerExperience.ended_on,
    CareerExperience.description,
    CareerExperience.url,
)

_EVIDENCE_COLUMNS = (
    CareerEvidence.id,
    CareerEvidence.user_id,
    CareerEvidence.evidence_type,
    CareerEvidence.title,
    CareerEvidence.description,
    CareerEvidence.occurred_on,
    CareerEvidence.project_id,
    CareerEvidence.skill_id,
    CareerEvidence.repository_id,
    CareerEvidence.source,
)


async def _profiles(session: AsyncSession, owner_id: uuid.UUID) -> list[dict[str, object]]:
    """Every stored profile row for one account."""
    result = await session.execute(
        select(*_PROFILE_COLUMNS).where(CareerProfile.user_id == owner_id)
    )
    return [dict(row._mapping) for row in result.all()]


async def _experiences(session: AsyncSession, owner_id: uuid.UUID) -> list[dict[str, object]]:
    """Every stored dated record for one account, oldest start date first."""
    result = await session.execute(
        select(*_EXPERIENCE_COLUMNS)
        .where(CareerExperience.user_id == owner_id)
        .order_by(CareerExperience.started_on.asc(), CareerExperience.id.asc())
    )
    return [dict(row._mapping) for row in result.all()]


async def _evidence_rows(session: AsyncSession, owner_id: uuid.UUID) -> list[dict[str, object]]:
    """Every stored evidence row for one account, oldest first."""
    result = await session.execute(
        select(*_EVIDENCE_COLUMNS)
        .where(CareerEvidence.user_id == owner_id)
        .order_by(CareerEvidence.occurred_on.asc(), CareerEvidence.id.asc())
    )
    return [dict(row._mapping) for row in result.all()]


#: The three career event types this service writes. Named in full rather than
#: filtered on a prefix so an event added to the reconciliation shows up in this tuple
#: and fails the assertion rather than being filtered past it.
_CAREER_EVENTS = (
    ActivityEvent.CAREER_PROFILE_UPDATED.value,
    ActivityEvent.CAREER_EVIDENCE_ADDED.value,
    ActivityEvent.CAREER_EVIDENCE_UPDATED.value,
)


async def _events(session: AsyncSession, user_id: uuid.UUID) -> list[dict[str, object]]:
    """This account's career history rows, with their metadata.

    Filtered to the three career events rather than to the whole feed, and left in
    the database's own order — the claim under test is "this fact was recorded",
    which a count answers; the order the database chose to write three transactions
    in is not a fact about the product.
    """
    result = await session.execute(
        select(ActivityLog.event_type, ActivityLog.metadata_).where(
            ActivityLog.user_id == user_id,
            ActivityLog.event_type.in_(_CAREER_EVENTS),
        )
    )
    return [{"event_type": str(row[0]), "metadata": row[1]} for row in result.all()]


def _event_counts(events: list[dict[str, object]]) -> Counter[str]:
    """``{event type: how many}`` for one account's history rows."""
    counts: Counter[str] = Counter()
    for event in events:
        counts[str(event["event_type"])] += 1
    return counts


# ---------------------------------------------------------------------------
# (a) The profile upsert
# ---------------------------------------------------------------------------


async def test_writing_a_profile_twice_leaves_exactly_one_row_and_the_second_write_wins(
    db_session: AsyncSession,
) -> None:
    """Two ``PUT``s, one row, and the second write's content is what survives.

    This is the assertion the unique ``career_profiles.user_id`` exists to make
    possible, and it is asserted by **counting rows** rather than by comparing the
    two returned shapes. Comparing shapes would pass for a service that inserted
    twice and returned the newer row from each call, which is exactly the failure:
    the page would render correctly and the account would carry two answers to every
    question about its career.

    The second write is a *partial* one — only the headline moves — and the surviving
    row keeps the ``target_role`` and the links from the first. A blind overwrite
    would blank them, and a blind insert would have raised on the constraint instead.
    The event is written once per ``PUT``, so two writes mean two rows in the feed:
    both attempts happened, and the feed records attempts.

    The derived figures, from the two writes above:

    * ``profile_rows == 1`` — the whole claim;
    * ``target_role == "Backend Engineer"`` and ``links == ["https://ada.test"]``
      carried forward from the first write;
    * ``headline == "Backend, mostly data"`` — the second write's value;
    * ``created_at`` unchanged by the second write, because the row was updated
      rather than replaced;
    * two ``career_profile_updated`` events, one per ``PUT``.
    """
    owner = await _owner(db_session)
    service = _service(db_session)

    first = await service.upsert_profile(
        owner=owner,
        values={
            "target_role": "Backend Engineer",
            "headline": "Backend, some data",
            "links": ["https://ada.test"],
        },
    )
    created_at = first.created_at
    second = await service.upsert_profile(owner=owner, values={"headline": "Backend, mostly data"})

    assert first.id == second.id, "the second PUT must revise the first profile"
    stored = await _profiles(db_session, owner.id)
    assert len(stored) == 1
    assert stored[0]["headline"] == "Backend, mostly data"
    assert stored[0]["target_role"] == "Backend Engineer"
    assert stored[0]["links"] == ["https://ada.test"]
    assert stored[0]["created_at"] == created_at

    events = await _events(db_session, owner.id)
    assert _event_counts(events) == {ActivityEvent.CAREER_PROFILE_UPDATED.value: 2}
    assert all(event["metadata"]["profile_id"] == str(first.id) for event in events)


async def test_an_empty_profile_write_is_refused_and_stores_no_row(
    db_session: AsyncSession,
) -> None:
    """A ``PUT`` with no fields is a validation error, not a blank profile.

    The repository would refuse an empty mapping, and this service turns that
    ``ValueError`` into the 422 the route documents rather than letting it reach the
    client as an unhandled 500. The count assertion is the point: an account that
    sent an empty body must not end up holding the one profile it is allowed to
    have, written with nothing in it.
    """
    owner = await _owner(db_session)
    service = _service(db_session)

    with pytest.raises(ValidationError):
        await service.upsert_profile(owner=owner, values={})

    assert await _profiles(db_session, owner.id) == []
    assert await _events(db_session, owner.id) == []


# ---------------------------------------------------------------------------
# (b) Ownership: 404, never 403
# ---------------------------------------------------------------------------


async def test_another_accounts_career_rows_are_not_found_and_never_appear(
    db_session: AsyncSession,
) -> None:
    """Ada's profile, record and evidence are invisible to Grace — and all 404.

    Every entry point is exercised, because each one could have been written with
    the owner predicate missing and the *detail* would still have been private: the
    record detail read, the record edit, the record delete, the evidence detail
    read, the evidence edit and the evidence delete. Ownership is a predicate in the
    ``WHERE`` clause rather than a filter over a loaded page, so the foreign row is
    never loaded at all.

    The foreign id and an id nobody ever issued raise the **same message**, entry
    point by entry point. That is what stops the endpoint being an existence oracle:
    if the answers differed, a caller could enumerate other people's career ids by
    comparing them.

    Grace also gets an *empty* career rather than a partial view of Ada's — her
    profile read is ``None``, her timeline has no rows and her evidence list has
    none — and Ada's rows are byte-identical afterwards, so none of the refusals
    touched them.
    """
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    ada_service = _service(db_session)
    today = await _db_now(db_session)

    profile = await ada_service.upsert_profile(owner=ada, values={"headline": "Ada"})
    record = await ada_service.create_experience(
        owner=ada, kind="experience", title="Backend Engineer", started_on=today.date()
    )
    evidence = await ada_service.create_evidence(
        owner=ada,
        evidence_type=CareerEvidenceType.ACHIEVEMENT.value,
        title="Shipped the billing rewrite",
        occurred_on=today.date(),
    )
    grace_service = _service(db_session)
    unknown = uuid.uuid4()

    with pytest.raises(NotFoundError) as read_record:
        await grace_service.get_experience(owner=grace, experience_id=record.id)
    with pytest.raises(NotFoundError) as unknown_record:
        await grace_service.get_experience(owner=grace, experience_id=unknown)
    with pytest.raises(NotFoundError) as edit_record:
        await grace_service.update_experience(
            owner=grace, experience_id=record.id, values={"title": "Hijacked"}
        )
    with pytest.raises(NotFoundError) as remove_record:
        await grace_service.delete_experience(owner=grace, experience_id=record.id)
    with pytest.raises(NotFoundError) as read_evidence:
        await grace_service.get_evidence(owner=grace, evidence_id=evidence.id)
    with pytest.raises(NotFoundError) as unknown_evidence:
        await grace_service.get_evidence(owner=grace, evidence_id=unknown)
    with pytest.raises(NotFoundError) as edit_evidence:
        await grace_service.update_evidence(
            owner=grace, evidence_id=evidence.id, values={"title": "Hijacked"}
        )
    with pytest.raises(NotFoundError) as remove_evidence:
        await grace_service.delete_evidence(owner=grace, evidence_id=evidence.id)

    assert (
        str(read_record.value) == str(unknown_record.value) == ("That career record was not found.")
    )
    assert str(edit_record.value) == str(remove_record.value) == str(read_record.value)
    assert (
        str(read_evidence.value)
        == str(unknown_evidence.value)
        == ("That career evidence was not found.")
    )
    assert str(edit_evidence.value) == str(remove_evidence.value) == str(read_evidence.value)

    # Grace's own reads are empty rather than carrying Ada's rows.
    assert await grace_service.get_profile(owner=grace) is None
    assert (await grace_service.list_experience(owner=grace)).items == []
    assert (await grace_service.list_evidence(owner=grace)).items == []
    grace_summary = await grace_service.summary(owner=grace)
    assert grace_summary.has_profile is False
    assert grace_summary.record_count == 0
    assert grace_summary.evidence_count == 0
    assert grace_summary.has_data is False

    # Ada's rows are untouched by any of it.
    assert len(await _profiles(db_session, ada.id)) == 1
    assert [row["title"] for row in await _experiences(db_session, ada.id)] == ["Backend Engineer"]
    assert [row["title"] for row in await _evidence_rows(db_session, ada.id)] == [
        "Shipped the billing rewrite"
    ]
    assert (await _profiles(db_session, ada.id))[0]["id"] == profile.id


async def test_another_accounts_record_cannot_be_pointed_at_by_new_evidence(
    db_session: AsyncSession,
) -> None:
    """Grace's evidence may not name Ada's project, skill or repository.

    Each ``*_id`` on an evidence row is user input, and each is a pointer onto
    somebody else's row unless it is resolved through an owner-scoped lookup before
    the write. A 403 would confirm the id exists; the answer is the same not-found
    every other foreign id gets, and the refusal happens **before** the write, so
    Grace is left with no evidence at all rather than with one row per failed
    attempt.
    """
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    ada_project = await AnalyticsSeed(db_session, ada).project(name="Atlas")
    ada_skill = await LearningRepository(db_session).create_skill(ada.id, name="Rust")
    service = _service(db_session)
    today = (await _db_now(db_session)).date()

    with pytest.raises(NotFoundError) as project:
        await service.create_evidence(
            owner=grace,
            evidence_type=CareerEvidenceType.PROJECT_COMPLETED.value,
            title="Finished somebody else's project",
            occurred_on=today,
            project_id=ada_project.id,
        )
    with pytest.raises(NotFoundError) as skill:
        await service.create_evidence(
            owner=grace,
            evidence_type=CareerEvidenceType.SKILL_ACTIVITY.value,
            title="Somebody else's skill",
            occurred_on=today,
            skill_id=ada_skill.id,
        )

    assert str(project.value) == "That project was not found."
    assert str(skill.value) == "That skill was not found."
    assert await _evidence_rows(db_session, grace.id) == []
    assert await _events(db_session, grace.id) == []


# ---------------------------------------------------------------------------
# (c) Dated records
# ---------------------------------------------------------------------------


async def test_a_record_that_ends_before_it_starts_is_a_validation_error_and_stores_nothing(
    db_session: AsyncSession,
) -> None:
    """``ended_on`` a day before ``started_on`` is refused before the write.

    The table would refuse it too, at
    ``ck_career_experience_dates_in_order`` — but it refuses it as an
    ``IntegrityError`` carrying a constraint name, which is a stack trace rather
    than a sentence. The service checks first so the caller gets the 422 the route
    documents, and the count assertion is what proves the ordering matters: the
    account's timeline is still empty afterwards, so nothing half-written is left
    behind.

    The same check runs on a **patch**, against the merged record. The patch below
    supplies only ``ended_on``, which is the case a naive check on the patch's own
    values would wave through — the stored start date is in 2020 and the patch's end
    date is in 2019.
    """
    owner = await _owner(db_session)
    service = _service(db_session)

    with pytest.raises(ValidationError) as backwards:
        await service.create_experience(
            owner=owner,
            kind="experience",
            title="Backend Engineer",
            started_on=date(2020, 1, 1),
            ended_on=date(2019, 12, 31),
        )

    assert str(backwards.value) == "An ended date may not be earlier than the started date."
    assert await _experiences(db_session, owner.id) == []

    current = await service.create_experience(
        owner=owner, kind="experience", title="Backend Engineer", started_on=date(2020, 1, 1)
    )
    with pytest.raises(ValidationError) as patched_backwards:
        await service.update_experience(
            owner=owner,
            experience_id=current.id,
            values={"ended_on": date(2019, 12, 31)},
        )

    assert str(patched_backwards.value) == str(backwards.value)
    assert [row["ended_on"] for row in await _experiences(db_session, owner.id)] == [None]


async def test_the_timeline_lists_records_by_kind_and_counts_the_open_ones(
    db_session: AsyncSession,
) -> None:
    """Three records across the three kinds, and the header describes all of them.

    Derived from the fixture: an education from 2014 to 2017, a current role started
    in 2022 with **no end date**, and a certification from 2019 to 2020. So the page
    carries ``total == 3``, ``by_kind`` is ``{education: 1, experience: 1,
    certification: 1}`` and ``current_count == 1`` — the open role and nothing else.

    The page size is two on purpose, because the claim being tested is that the
    header describes **every matching record and not the page**: with ``limit=2``
    the page holds two rows and the third is only visible through ``total``,
    ``by_kind`` and ``current_count``. A tally computed from the page would report
    two records and one open one.

    ``ended_on`` being null is how a *current* role says so, so the record with no
    end date is the one the counter is looking for — not a record with an unknown
    end date, which the schema stores the same way and this file cannot distinguish.
    """
    owner = await _owner(db_session)
    service = _service(db_session)

    await service.create_experience(
        owner=owner,
        kind="education",
        title="BSc Computer Science",
        organisation="A university",
        started_on=date(2014, 9, 1),
        ended_on=date(2017, 6, 30),
    )
    await service.create_experience(
        owner=owner,
        kind="experience",
        title="Backend Engineer",
        organisation="A company",
        started_on=date(2022, 1, 1),
    )
    await service.create_experience(
        owner=owner,
        kind="certification",
        title="AWS Solutions Architect Associate",
        started_on=date(2019, 3, 1),
        ended_on=date(2020, 3, 1),
    )

    page = await service.list_experience(owner=owner, limit=2)

    assert len(page.items) == 2
    assert page.total == 3
    assert page.by_kind == {"education": 1, "experience": 1, "certification": 1}
    assert page.current_count == 1

    # And the filter narrows the page *and* the tallies together, so a filtered
    # header can never describe the unfiltered set.
    certified = await service.list_experience(owner=owner, kind="certification")
    assert certified.total == 1
    assert certified.by_kind == {"education": 0, "experience": 0, "certification": 1}
    assert certified.current_count == 0


# ---------------------------------------------------------------------------
# (d) Evidence
# ---------------------------------------------------------------------------


async def test_a_second_row_with_the_same_source_identity_is_a_conflict_not_a_crash(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """The row the unique constraint would refuse becomes a 409 in words.

    Two evidence rows naming the same project, with the same type and the same
    source, are the identity ``uq_career_evidence_source_identity`` refuses. The
    service asks
    :meth:`CareerRepository.find_duplicate_evidence` first so the caller gets a
    sentence rather than a constraint name, and the count assertion proves the
    refusal happened **before** the write: one row is stored, not two and not a
    half-written pair.

    The other half of the same rule is asserted here, because it is the half a naive
    implementation gets wrong: a second **manual** achievement has all three foreign
    keys null, and nulls do not collide in a btree unique index — so both rows
    coexist. A service that translated the null columns to ``IS NULL`` would refuse
    the second one and contradict its own schema.
    """
    owner = await _owner(db_session)
    grace = await _owner(db_session, "grace")
    service = _service(db_session)
    project = await AnalyticsSeed(db_session, owner).project(name="Atlas")
    skill = await LearningRepository(db_session).create_skill(owner.id, name="Rust")
    repository_id = await _registered_repository_id(db_session, owner.id, tmp_path, "atlas")
    today = (await _db_now(db_session)).date()

    # A derived row naming all three of its sources. The constraint is
    # ``(user_id, evidence_type, source, project_id, skill_id, repository_id)``, and
    # **nulls do not collide in a btree unique index** — so a conflict needs all
    # three pointers present, which is exactly what the second insert below repeats.
    first = await service.create_evidence(
        owner=owner,
        evidence_type=CareerEvidenceType.PROJECT_COMPLETED.value,
        title="Finished Atlas",
        occurred_on=today,
        project_id=project.id,
        skill_id=skill.id,
        repository_id=repository_id,
        source="project",
    )

    with pytest.raises(ConflictError) as duplicate:
        await service.create_evidence(
            owner=owner,
            evidence_type=CareerEvidenceType.PROJECT_COMPLETED.value,
            title="Finished Atlas again",
            occurred_on=today,
            project_id=project.id,
            skill_id=skill.id,
            repository_id=repository_id,
            source="project",
        )
    assert str(duplicate.value) == "That evidence is already recorded from the same source."

    # A different type over the same three sources is a different row: the type is
    # part of the identity, and "finished Atlas" and "learned from Atlas" are
    # different claims.
    second_type = await service.create_evidence(
        owner=owner,
        evidence_type=CareerEvidenceType.SKILL_ACTIVITY.value,
        title="Learned from Atlas",
        occurred_on=today,
        project_id=project.id,
        skill_id=skill.id,
        repository_id=repository_id,
        source="project",
    )
    assert second_type.id != first.id

    # Two manual achievements, both foreign keys null, both stored.
    await service.create_evidence(
        owner=owner,
        evidence_type=CareerEvidenceType.ACHIEVEMENT.value,
        title="Mentored two interns",
        occurred_on=today,
    )
    await service.create_evidence(
        owner=owner,
        evidence_type=CareerEvidenceType.ACHIEVEMENT.value,
        title="Ran a workshop",
        occurred_on=today,
    )

    rows = await _evidence_rows(db_session, owner.id)
    assert len(rows) == 4
    assert sum(1 for row in rows if row["title"].startswith("Finished Atlas")) == 1
    assert sum(1 for row in rows if row["project_id"] is None) == 2
    # Grace may hold the identical identity: it is a different person's claim.
    await service.create_evidence(
        owner=grace,
        evidence_type=CareerEvidenceType.PROJECT_COMPLETED.value,
        title="Finished Atlas",
        occurred_on=today,
        source="project",
    )
    assert len(await _evidence_rows(db_session, grace.id)) == 1

    events = _event_counts(await _events(db_session, owner.id))
    assert events[ActivityEvent.CAREER_EVIDENCE_ADDED.value] == 4
    assert ActivityEvent.CAREER_EVIDENCE_UPDATED.value not in events


async def test_evidence_without_a_date_is_refused_because_a_placeholder_would_be_fabricated(
    db_session: AsyncSession,
) -> None:
    """``occurred_on`` is required, on the create and on the patch that clears it.

    Evidence is a thing that happened on a day. A row with no day cannot be placed in
    a timeline, and the two ways to make it renderable — a placeholder date or a
    silent default — would both be dates the user never gave. So the service refuses
    rather than inventing one, and stores nothing in either case.

    The patch half matters as much as the create half: an explicit
    ``occurred_on: null`` is a request to *remove* the date from a row that has one,
    which is the same fabrication in the other direction.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    today = (await _db_now(db_session)).date()

    with pytest.raises(ValidationError) as undated:
        await service.create_evidence(
            owner=owner,
            evidence_type=CareerEvidenceType.ACHIEVEMENT.value,
            title="Something happened",
        )
    assert str(undated.value) == "Career evidence must carry the date it happened on."

    stored = await service.create_evidence(
        owner=owner,
        evidence_type=CareerEvidenceType.ACHIEVEMENT.value,
        title="Something happened",
        occurred_on=today,
    )
    with pytest.raises(ValidationError):
        await service.update_evidence(
            owner=owner, evidence_id=stored.id, values={"occurred_on": None}
        )

    assert [row["occurred_on"] for row in await _evidence_rows(db_session, owner.id)] == [today]


async def test_the_evidence_cap_is_answered_before_the_insert(
    db_session: AsyncSession, make_settings
) -> None:
    """At a cap of one, the second piece of evidence is a conflict that names the limit.

    The cap is checked by counting rows rather than by discovering the overflow
    afterwards, so the refusal arrives as a 409 with the limit in the sentence. The
    count assertion is the rest of it: a refused write leaves the account holding
    exactly the row it had, and no half-written second one.
    """
    settings = make_settings(CAREER_MAX_EVIDENCE="1")
    owner = await _owner(db_session)
    service = _service(db_session, settings=settings)
    today = (await _db_now(db_session)).date()

    await service.create_evidence(
        owner=owner,
        evidence_type=CareerEvidenceType.ACHIEVEMENT.value,
        title="First",
        occurred_on=today,
    )
    with pytest.raises(ConflictError) as at_cap:
        await service.create_evidence(
            owner=owner,
            evidence_type=CareerEvidenceType.ACHIEVEMENT.value,
            title="Second",
            occurred_on=today,
        )

    assert str(at_cap.value) == (
        "This account already has the maximum of 1 career evidence records."
    )
    assert len(await _evidence_rows(db_session, owner.id)) == 1


async def test_editing_evidence_writes_the_words_and_never_the_provenance(
    db_session: AsyncSession,
) -> None:
    """A correction changes the title and the date, and the pointer survives it.

    The evidence foreign keys are ``SET NULL`` so the claim outlives the thing it
    claims, and a correction must not quietly unmake it: the row keeps its project,
    its source and its date unless the user changed them. One
    ``career_evidence_updated`` event is written and it names the field names the
    caller actually sent — ids and field names only, never the user's words, which
    belong on the row the event points at.

    The duplicate re-check runs only when the identity moves. Here it does not, so a
    rename cannot be mistaken for a second record.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    project = await AnalyticsSeed(db_session, owner).project(name="Atlas")
    today = (await _db_now(db_session)).date()
    stored = await service.create_evidence(
        owner=owner,
        evidence_type=CareerEvidenceType.FEATURE_SHIPPED.value,
        title="Shipped invoicing",
        occurred_on=today,
        project_id=project.id,
        source="project",
    )

    updated = await service.update_evidence(
        owner=owner,
        evidence_id=stored.id,
        values={"title": "Shipped the invoicing rewrite", "description": "Two weeks of work"},
    )

    assert updated.title == "Shipped the invoicing rewrite"
    assert updated.project_id == project.id
    assert updated.source == "project"
    row = (await _evidence_rows(db_session, owner.id))[0]
    assert row["title"] == "Shipped the invoicing rewrite"
    assert row["description"] == "Two weeks of work"
    assert row["project_id"] == project.id
    assert row["source"] == "project"

    events = await _events(db_session, owner.id)
    assert _event_counts(events)[ActivityEvent.CAREER_EVIDENCE_UPDATED.value] == 1
    update_event = next(
        event
        for event in events
        if event["event_type"] == ActivityEvent.CAREER_EVIDENCE_UPDATED.value
    )
    assert update_event["metadata"] == {
        "evidence_id": str(stored.id),
        "fields": ["description", "title"],
    }


# ---------------------------------------------------------------------------
# (e) The honest empty state
# ---------------------------------------------------------------------------


async def test_an_account_with_no_career_reads_as_an_absence_not_an_invented_profile(
    db_session: AsyncSession,
) -> None:
    """Nothing written anywhere: a 404-free ``None``, zeroes, and a sentence that says so.

    This is the cold-start claim in one test. The profile read returns ``None``
    rather than raising — an account that has never written one is in a state the UI
    has to render, not an absence it has to explain, and a 404 would say "you asked
    for something that does not exist" about the one resource the page always asks
    for. The summary reports ``has_data=False``, every count at zero, and the
    sentence says there is nothing to summarise.

    **No profile is invented.** The row count is asserted directly: a service that
    "helped" by creating an empty profile would render a plausible-looking page and
    would have written the first career row NEXUS ever wrote for this account.

    The feature vector beside it is asserted here too, because the same absence has
    to reach it: ``project_activity`` is ``None`` — no repository has ever been
    scanned, so ``0`` would assert a repository exists and carries no commits.
    """
    owner = await _owner(db_session)
    service = _service(db_session)

    assert await service.get_profile(owner=owner) is None
    summary = await service.summary(owner=owner)

    assert summary.has_profile is False
    assert summary.has_data is False
    assert summary.target_role is None
    assert summary.link_count == 0
    assert summary.record_count == 0
    assert summary.evidence_count == 0
    assert summary.evidence_in_window == 0
    assert summary.manual_evidence_count == 0
    assert summary.linked_project_count == 0
    assert summary.project_count == 0
    assert summary.completed_project_count == 0
    assert summary.repository_count == 0
    assert summary.skills_with_evidence == 0
    assert summary.learning_activity_count == 0
    assert summary.latest_evidence_on is None
    assert summary.window_days == WINDOW
    assert summary.summary == (
        "No career profile has been written and no career records or evidence have "
        "been added yet, so there is nothing to summarise."
    )

    vector = await service.features(owner=owner)
    assert vector.schema_version == "career_features.v1"
    assert vector.features.projects_completed == 0
    assert vector.features.project_activity is None
    assert vector.features.repositories == 0
    assert vector.features.relevant_skill_evidence == 0
    assert vector.features.learning_activity == 0
    assert vector.features.portfolio_evidence_count == 0

    assert await _profiles(db_session, owner.id) == []


async def test_the_summary_counts_records_evidence_and_the_provenance_between_them(
    db_session: AsyncSession,
) -> None:
    """Two projects (one completed), two records, four evidence rows, one of them old.

    Derived from the fixture:

    * ``project_count == 2`` and ``completed_project_count == 1``, read from the
      project's own status column rather than inferred from the evidence table;
    * ``record_count == 2`` — education and a current role;
    * ``evidence_count == 4`` across the whole history, of which ``evidence_in_window
      == 3``: one row is dated 40 days ago, which is outside a 30-day window, and the
      boundary is a *date* comparison because ``occurred_on`` carries no time;
    * ``manual_evidence_count == 2`` — the two rows the user typed, against four
      rows in total. This is the figure that separates what the person wrote from
      what the system observed, and it is the one a career page is obliged to state
      rather than blur;
    * ``linked_project_count == 2`` — two distinct projects named by four rows, so
      the distinct count and the row count are not the same number;
    * ``skills_with_evidence == 1`` — one skill carries three evidence rows and is
      counted once;
    * ``latest_evidence_on`` is the most recent day any evidence is dated, which is
      inside the window rather than being the newest by insertion;
    * ``has_data`` is true, and the sentence carries its own figures.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    seed = AnalyticsSeed(db_session, owner)
    now = await _db_now(db_session)
    today = now.date()
    active = await seed.project(name="Atlas", status=ProjectStatus.ACTIVE.value)
    completed = await seed.project(name="Beacons", status=ProjectStatus.COMPLETED.value)
    skill = await LearningRepository(db_session).create_skill(owner.id, name="Rust")

    await service.upsert_profile(
        owner=owner,
        values={
            "target_role": "Backend Engineer",
            "links": ["https://ada.test", "https://github.com/ada"],
        },
    )
    await service.create_experience(
        owner=owner, kind="education", title="BSc Computer Science", started_on=date(2014, 9, 1)
    )
    await service.create_experience(
        owner=owner, kind="experience", title="Backend Engineer", started_on=date(2022, 1, 1)
    )
    await service.create_evidence(
        owner=owner,
        evidence_type=CareerEvidenceType.PROJECT_COMPLETED.value,
        title="Finished Beacons",
        occurred_on=today,
        project_id=completed.id,
        source="project",
    )
    await service.create_evidence(
        owner=owner,
        evidence_type=CareerEvidenceType.SKILL_ACTIVITY.value,
        title="Wrote the migration in Rust",
        occurred_on=today - timedelta(days=5),
        skill_id=skill.id,
        source="skill",
    )
    # Deliberately **manual**: the user pointed this one at a skill themselves, which
    # is a different claim from the derived row above and is counted as theirs.
    await service.create_evidence(
        owner=owner,
        evidence_type=CareerEvidenceType.SKILL_ACTIVITY.value,
        title="Reviewed a Rust crate",
        occurred_on=today - timedelta(days=40),
        skill_id=skill.id,
    )
    await service.create_evidence(
        owner=owner,
        evidence_type=CareerEvidenceType.ACHIEVEMENT.value,
        title="Mentored two interns",
        occurred_on=today,
        project_id=active.id,
    )

    summary = await service.summary(owner=owner, window_days=WINDOW)

    assert summary.has_profile is True
    assert summary.target_role == "Backend Engineer"
    assert summary.link_count == 2
    assert summary.project_count == 2
    assert summary.completed_project_count == 1
    assert summary.record_count == 2
    assert summary.evidence_count == 4
    assert summary.evidence_in_window == 3
    assert summary.manual_evidence_count == 2
    assert summary.linked_project_count == 2
    assert summary.skills_with_evidence == 1
    assert summary.latest_evidence_on == today
    assert summary.has_data is True
    assert summary.window_days == WINDOW
    assert "4 career evidence records" in summary.summary
    assert "2 of them entered by hand" in summary.summary

    # And the list header beside the same rows agrees with the summary.
    evidence = await service.list_evidence(owner=owner)
    assert evidence.total == 4
    assert evidence.by_type[CareerEvidenceType.SKILL_ACTIVITY.value] == 2
    assert evidence.by_type[CareerEvidenceType.ACHIEVEMENT.value] == 1
    assert evidence.by_type[CareerEvidenceType.CERTIFICATION.value] == 0
    assert evidence.by_source == {"project": 1, "skill": 1, "manual": 2}
    assert evidence.manual_count == 2


# ---------------------------------------------------------------------------
# (f) The ML-ready feature vector
# ---------------------------------------------------------------------------


async def test_the_feature_vector_reports_null_for_project_activity_it_could_not_compute(
    db_session: AsyncSession, tmp_path
) -> None:
    """Two accounts, two reasons for ``None``, and one genuine zero that survives.

    ``project_activity`` is recorded commits per completed project, and it is
    ``None`` in two distinct situations — which is exactly why it cannot be ``0``:

    * **Ada** has one completed project and no repository at all. There is nothing
      that was ever scanned, so a ``0`` would be a claim about a repository that
      exists and carries no commits, when the truth is that nobody has looked.
    * **Grace** has a completed project and a repository that has never been scanned.
      Same answer for the same reason, and the sharper case: here a repository row
      *does* exist, so the count of registered repositories is 1 while
      ``project_activity`` is still ``None``. A reader comparing the two figures can
      see that the zero was refused rather than measured.

    The other five figures are counts of the account's own rows and records, so a
    real zero is a real zero and is asserted as ``0`` — including ``repositories``,
    where Ada's ``0`` genuinely means "nobody registered one".

    Ada's ``relevant_skill_evidence`` is 2 and ``portfolio_evidence_count`` is 1:
    two rows point at a tracked skill, and one row was entered by hand against two
    links on the profile. The two skill rows name **different** skills, and that
    is forced rather than incidental: ``uq_career_evidence_source_identity`` is
    ``NULLS NOT DISTINCT``, so two rows identifying the same skill — same type,
    same source, same ``skill_id``, the other two pointers null — are one row
    twice, and the second is refused as a conflict. Under the table-level
    ``UNIQUE`` that preceded it the pair inserted cleanly, which is precisely how
    the same project could be "derived" into two identical evidence rows. The
    figure under test is the *count of rows pointing at a tracked skill*, and two
    skills are the honest way to reach it.
    """
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    service = _service(db_session)
    learning = LearningRepository(db_session)
    rust = await learning.create_skill(ada.id, name="Rust")
    typescript = await learning.create_skill(ada.id, name="TypeScript")
    await AnalyticsSeed(db_session, ada).project(
        name="Beacons", status=ProjectStatus.COMPLETED.value
    )
    await AnalyticsSeed(db_session, grace).project(
        name="Compass", status=ProjectStatus.COMPLETED.value
    )
    # A registered work tree that has never been read. ``local_path`` is whatever the
    # repository is handed; ``last_scanned_at`` is left null, which is how "registered
    # but never scanned" is stored.
    await DeveloperRepository(db_session).create_repository(
        grace.id, name="compass", local_path=str(tmp_path / "compass"), project_id=None
    )
    await service.upsert_profile(owner=ada, values={"links": ["https://ada.test"]})
    today = (await _db_now(db_session)).date()
    for skill, title in ((rust, "Rust session"), (typescript, "TypeScript session")):
        await service.create_evidence(
            owner=ada,
            evidence_type=CareerEvidenceType.SKILL_ACTIVITY.value,
            title=title,
            occurred_on=today - timedelta(days=2),
            skill_id=skill.id,
            source="skill",
        )
    await service.create_evidence(
        owner=ada,
        evidence_type=CareerEvidenceType.ACHIEVEMENT.value,
        title="Mentored two interns",
        occurred_on=today,
    )

    ada_vector = await service.features(owner=ada, window_days=WINDOW)
    assert ada_vector.window_days == WINDOW
    assert ada_vector.generated_at is not None
    assert ada_vector.features.projects_completed == 1
    assert ada_vector.features.project_activity is None, "no repository has ever been scanned"
    assert ada_vector.features.repositories == 0
    assert ada_vector.features.relevant_skill_evidence == 2
    assert ada_vector.features.learning_activity == 0
    assert ada_vector.features.portfolio_evidence_count == 2  # one manual row, one link

    grace_vector = await service.features(owner=grace)
    assert grace_vector.features.projects_completed == 1
    assert grace_vector.features.project_activity is None
    assert grace_vector.features.repositories == 1
    assert grace_vector.features.portfolio_evidence_count == 0


async def test_project_activity_is_measured_once_a_scanned_repository_exists(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Four commits on a scanned repository, one completed project: ``4.0``.

    The control for the ``None`` above, and the reason ``None`` is trustworthy: the
    figure is ``None`` because it was *not computed*, not because the service
    refuses to produce a number here. Once a repository linked to one of the
    account's completed projects has actually been scanned, the same call returns
    ``round(4 / 1, 4) == 4.0``.

    The four commits are written through the repository's own upsert and the scan
    state through ``update_scan_state`` — the only call permitted to move
    ``last_scanned_at`` — because the claim is about what the join can see, and a
    fixture that set the column directly would be asserting a state NEXUS never
    produces. The fifth commit is dated 40 days ago, outside the 30-day window, so
    the numerator is 4 and not 5: **a window that silently counted the whole history
    would be a different feature wearing the same name.**

    ``repository_activity`` is a ratio of recorded commits to completed projects. It
    says that four commits were recorded against one finished project; it does not
    say the project was four commits' worth of work.
    """
    owner = await _owner(db_session)
    service = _service(db_session)
    project = await AnalyticsSeed(db_session, owner).project(
        name="Beacons", status=ProjectStatus.COMPLETED.value
    )
    developer = DeveloperRepository(db_session)
    repository = await developer.create_repository(
        owner.id,
        name="beacons",
        local_path=str(tmp_path / "beacons"),
        project_id=project.id,
    )
    now = await _db_now(db_session)
    await developer.upsert_commits(
        owner.id,
        repository.id,
        [
            {
                "commit_hash": f"{label}",
                "short_hash": f"{label}",
                "committed_at": moment,
                "message": f"commit {label}",
                "additions": 3,
                "deletions": 1,
                "files_changed": 1,
                "branch": "main",
            }
            for label, moment in (
                ("inside-1", now - timedelta(days=1)),
                ("inside-2", now - timedelta(days=3)),
                ("inside-3", now - timedelta(days=9)),
                ("inside-4", now - timedelta(days=25)),
                ("outside", now - timedelta(days=40)),
            )
        ],
    )
    await developer.update_scan_state(
        owner.id,
        repository.id,
        {
            "last_scanned_at": now,
            "last_scan_status": GitScanStatus.OK.value,
            "commit_count": 5,
            "latest_commit_at": now - timedelta(days=1),
        },
    )

    vector = await service.features(owner=owner, window_days=WINDOW)

    assert vector.features.projects_completed == 1
    assert vector.features.repositories == 1
    assert vector.features.project_activity == 4.0
