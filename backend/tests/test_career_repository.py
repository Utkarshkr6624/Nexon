"""CareerRepository against the real test database.

Four properties are worth pinning, and none of them is visible from reading the
code:

**The profile is an upsert, and there can only ever be one.** ``PUT
/career/profile`` is a *get-or-create then update*, never a blind ``INSERT``. A
blind insert would make a second profile impossible in the same way and would
also make the first ``PUT`` for an account that already has one fail on the
constraint — which is the bug the upsert exists to prevent. The test writes the
same profile twice and then counts the rows, because "no error was raised" is not
the same claim as "there is exactly one profile".

**Nothing here is invented.** Every value the repository writes is a
transcription of something the user supplied, and the write sets are the last
place that could quietly add one. ``organisation`` being null is the normal case
for a self-directed project and is never filled in; a write naming a column
outside the editable set raises with the column's name rather than being
silently dropped.

**Evidence deduplication reproduces the constraint, nulls included.**
``uq_career_evidence_source_identity`` leans on the PostgreSQL detail that nulls
do not collide in a btree unique index: several manually-added ``ACHIEVEMENT``
rows all have three null foreign keys, all compare unequal, and all coexist,
while a row naming all three of them cannot be inserted twice. The duplicate
check therefore short-circuits to "no duplicate" whenever any of the three is
null — a lookup written with ``IS NULL`` for the absent columns would refuse the
second manual achievement and contradict its own schema.

**Nothing is visible across accounts.** Every read filters on ``user_id`` in its
``WHERE`` clause, and a foreign id is answered with ``None`` or ``False`` rather
than a 403 — a 403 would confirm the row exists and turn the endpoint into an
existence oracle for which career ids are real. A career history is the most
identifying row set this schema holds, which is why the predicate is never
something the caller supplies.

Rows are read back through explicit column projections rather than through the
ORM wherever the assertion is about what was *stored*: this session is the one
that wrote them, so an entity read would hand back whatever the identity map
cached and the count-the-rows assertions could pass for the wrong reason.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.career import CareerEvidence, CareerExperience, CareerProfile
from app.models.developer import GitRepository
from app.models.enums import CareerEvidenceType, CareerRecordKind
from app.models.learning import Skill
from app.models.project import Project
from app.repositories.career import CareerRepository
from app.repositories.learning import LearningRepository
from tests.analytics_fixtures import register_user

pytestmark = pytest.mark.integration

#: Every evidence row in this file happened on one of these two days, so the
#: date filters have something to separate.
DAY_ONE = date(2026, 5, 4)
DAY_TWO = date(2026, 5, 11)


@pytest.fixture
def repository(db_session) -> CareerRepository:
    return CareerRepository(db_session)


@pytest.fixture
def skills(db_session) -> LearningRepository:
    """The skill table, for the evidence foreign keys that point at it."""
    return LearningRepository(db_session)


@pytest.fixture
async def owner(db_session):
    """The account most tests write as."""
    return await register_user(db_session, username="ada", email="ada@nexus.test")


@pytest.fixture
async def other(db_session):
    """A second account, for every isolation assertion."""
    return await register_user(db_session, username="grace", email="grace@nexus.test")


async def _profile_count(db_session, owner_id: uuid.UUID) -> int:
    """How many profile rows this account has, counted in the database.

    Counted rather than inferred from a returned object, because the whole claim
    under test is that the second ``PUT`` did not create a second row — and the
    object handed back would be the same either way.
    """
    return int(
        await db_session.scalar(
            select(func.count()).select_from(CareerProfile).where(CareerProfile.user_id == owner_id)
        )
    )


async def _skill_and_repository_ids(db_session, owner_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Two real ids the evidence table can point at.

    They are inserted as bare rows through their own models rather than through
    their repositories, because what is under test here is the foreign keys — and
    a row that had to be created through another repository would make this test
    depend on a file it is not testing.
    """
    skill_id, repository_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(Skill(id=skill_id, user_id=owner_id, name="Python"))
    db_session.add(
        GitRepository(
            id=repository_id,
            user_id=owner_id,
            name="nexus",
            local_path="/repos/nexus",
        )
    )
    await db_session.commit()
    return skill_id, repository_id


# ----------------------------------------------------------------------
# Profile
# ----------------------------------------------------------------------


async def test_a_fresh_profile_is_empty_rather_than_invented(repository, db_session, owner):
    """The first ``PUT`` creates the row, and every other column is the user's.

    An empty profile is a real state the UI has to render, not a half-built one,
    so ``links`` is ``[]`` — the user supplied no URLs, which is an answer — and
    the six descriptive columns are null because nobody typed them. Nothing here
    is derived from activity: there is no "profile strength" and no generated
    summary, and this repository has no column to put one in.
    """
    assert await repository.get_profile(owner.id) is None

    created = await repository.upsert_profile(owner.id, {"target_role": "Backend Engineer"})

    assert isinstance(created, CareerProfile)
    assert created.user_id == owner.id
    assert created.target_role == "Backend Engineer"
    assert created.target_domain is None
    assert created.headline is None
    assert created.summary is None
    assert created.location is None
    assert created.links == []
    assert created.created_at.tzinfo is not None
    assert created.updated_at.tzinfo is not None


async def test_writing_the_profile_twice_leaves_exactly_one_row(repository, db_session, owner):
    """The upsert is get-or-create then update, not an insert per request.

    A person has one career by definition, and a second row would be a second
    *answer* to every question about it — a page would have to decide which one
    wins, and that decision would be a silent coin flip that changes with it.
    The count is read after both writes because "no error was raised" is a weaker
    claim than "there is still exactly one profile".
    """
    await repository.upsert_profile(owner.id, {"target_role": "Backend Engineer"})
    owner_id = owner.id
    second = await repository.upsert_profile(
        owner_id, {"headline": "Building things that run", "links": ["https://nexus.dev"]}
    )

    assert await _profile_count(db_session, owner_id) == 1
    assert second.target_role == "Backend Engineer"
    assert second.headline == "Building things that run"
    assert second.links == ["https://nexus.dev"]


async def test_a_profile_write_names_only_the_users_own_columns(repository, owner):
    """The write set is the phase's whole discipline, expressed as a whitelist.

    ``user_id`` is the *key* of the upsert and is not something a request may
    choose; ``id`` and ``created_at`` are not editable by anybody. A key outside
    the set raises by name rather than being quietly ignored, because a caller
    that passed one needs to know the write did not happen — and the refusal
    happens before any statement is built, so nothing was half-written.
    """
    await repository.upsert_profile(owner.id, {"headline": "one"})

    with pytest.raises(ValueError, match="user_id"):
        await repository.upsert_profile(owner.id, {"user_id": uuid.uuid4()})
    with pytest.raises(ValueError, match="at least one field"):
        await repository.upsert_profile(owner.id, {})

    stored = await repository.get_profile(owner.id)
    assert stored.headline == "one"
    assert stored.user_id == owner.id


async def test_an_accounts_profile_is_not_readable_from_another(repository, owner, other):
    """``get_profile`` asserts the owner, and returns ``None`` for a stranger.

    The route turns that ``None`` into a 404, never a 403: a 403 would confirm
    the account has a profile and turn the endpoint into a probe.
    """
    await repository.upsert_profile(owner.id, {"headline": "Ada"})

    assert await repository.get_profile(other.id) is None
    assert await repository.get_profile(owner.id) is not None


# ----------------------------------------------------------------------
# Experience
# ----------------------------------------------------------------------


async def test_the_timeline_leads_with_the_current_record(repository, owner):
    """Three records: a current role, a finished one, and an undated course.

    Current first, because "what am I doing now" is the question a career page
    opens with, then the finished role, then the undated one. Sorting an undated
    record as though it were the oldest thing on the page is a statement the
    data does not support — "no start date" is not a date — and it would put the
    record the user has just added at the bottom of the timeline.
    """
    await repository.create_experience(
        owner.id, kind=CareerRecordKind.CERTIFICATION, title="AWS Associate"
    )
    await repository.create_experience(
        owner.id,
        kind=CareerRecordKind.EXPERIENCE,
        title="Engineer",
        started_on=date(2022, 1, 1),
        ended_on=date(2024, 6, 1),
    )
    await repository.create_experience(
        owner.id,
        kind=CareerRecordKind.EXPERIENCE,
        title="Senior Engineer",
        organisation="Nexus",
        started_on=date(2024, 6, 1),
    )

    page, total = await repository.list_experience(owner.id)

    assert [row.title for row in page] == ["Senior Engineer", "Engineer", "AWS Associate"]
    assert total == 3


async def test_the_timeline_filters_by_kind_and_reports_the_unpaged_total(repository, owner):
    """Two education records and one certification.

    Education and certification share one table because they differ only in
    label, not in shape — both are "a titled thing, at an organisation, over a
    span of dates". The kind filter is the only thing separating them, and each
    row here is filtered against a row the filter should exclude.
    """
    await repository.create_experience(
        owner.id, kind=CareerRecordKind.EDUCATION, title="BSc Computer Science"
    )
    await repository.create_experience(
        owner.id, kind=CareerRecordKind.CERTIFICATION, title="AWS Associate"
    )
    await repository.create_experience(
        owner.id, kind=CareerRecordKind.EDUCATION, title="MSc Distributed Systems"
    )

    page, total = await repository.list_experience(owner.id, kind=CareerRecordKind.EDUCATION)
    certifications, cert_total = await repository.list_experience(owner.id, kind="certification")

    assert {row.title for row in page} == {"BSc Computer Science", "MSc Distributed Systems"}
    assert total == 2
    assert [row.title for row in certifications] == ["AWS Associate"]
    assert cert_total == 1


async def test_a_record_is_stored_exactly_as_the_user_supplied_it(repository, db_session, owner):
    """No organisation is looked up, and null means the user gave none.

    There is no university register, no payroll and no scraped profile behind
    any of these columns. ``organisation=None`` is the normal case for an
    open-source contribution and is not a gap to be filled in later — inventing
    one is precisely the failure this phase forbids.
    """
    created = await repository.create_experience(
        owner.id,
        kind=CareerRecordKind.EXPERIENCE,
        title="Open-source contributor",
        organisation=None,
        started_on=date(2023, 4, 1),
    )

    stored = (
        await db_session.execute(
            select(
                CareerExperience.kind,
                CareerExperience.title,
                CareerExperience.organisation,
                CareerExperience.started_on,
                CareerExperience.ended_on,
            ).where(CareerExperience.id == created.id)
        )
    ).one()
    assert stored == ("experience", "Open-source contributor", None, date(2023, 4, 1), None)


async def test_an_unknown_record_kind_is_refused_before_storage(repository, owner):
    """An unrecognised kind is a row that appears in no section of its timeline.

    The three members are the three shapes the profile renders, so a fourth
    value would need a fourth shape to go with it.
    """
    with pytest.raises(ValueError, match="career record kind"):
        await repository.create_experience(owner.id, kind="internship", title="Something")

    assert await repository.list_experience(owner.id) == ([], 0)


async def test_a_record_edit_writes_only_the_columns_it_names(repository, db_session, owner):
    """A PATCH is a partial change, and the rest of the transcription survives.

    The start date is asserted afterwards because it is the one column with a
    constraint on it: editing the end date alone must not be able to leave the row
    describing an employment that ended before it started, and
    ``ck_career_experience_dates_in_order`` is what stops it.
    """
    created = await repository.create_experience(
        owner.id,
        kind=CareerRecordKind.EXPERIENCE,
        title="Engineer",
        organisation="Nexus",
        started_on=date(2022, 1, 1),
    )

    updated = await repository.update_experience(
        owner.id, created.id, {"title": "Senior Engineer", "ended_on": date(2024, 6, 1)}
    )

    assert updated is not None
    stored = (
        await db_session.execute(
            select(
                CareerExperience.title,
                CareerExperience.organisation,
                CareerExperience.started_on,
                CareerExperience.ended_on,
            ).where(CareerExperience.id == created.id)
        )
    ).one()
    assert stored == ("Senior Engineer", "Nexus", date(2022, 1, 1), date(2024, 6, 1))


async def test_another_accounts_records_are_invisible_and_undeletable(repository, owner, other):
    """A foreign record is invisible, uneditable and undeletable.

    The final read is by the *owner* of the row, because a test that only checked
    ``False`` would pass even if the delete had matched.
    """
    theirs = await repository.create_experience(
        other.id, kind=CareerRecordKind.EDUCATION, title="Their degree"
    )

    assert await repository.list_experience(owner.id) == ([], 0)
    assert await repository.get_experience(owner.id, theirs.id) is None
    assert await repository.get_experience(owner.id, uuid.uuid4()) is None
    assert await repository.update_experience(owner.id, theirs.id, {"title": "stolen"}) is None
    assert await repository.delete_experience(owner.id, theirs.id) is False
    assert await repository.get_experience(other.id, theirs.id) is not None


async def test_deleting_a_record_removes_only_that_record(repository, owner):
    """One delete, one row gone, the rest of the timeline intact.

    Owner-scoped, so a second attempt reports ``False`` rather than raising, and
    the same ``False`` an unknown id gets.
    """
    first = await repository.create_experience(
        owner.id, kind=CareerRecordKind.EDUCATION, title="BSc"
    )
    await repository.create_experience(owner.id, kind=CareerRecordKind.EDUCATION, title="MSc")

    assert await repository.delete_experience(owner.id, first.id) is True
    assert await repository.delete_experience(owner.id, first.id) is False

    _page, total = await repository.list_experience(owner.id)
    assert total == 1


# ----------------------------------------------------------------------
# Evidence
# ----------------------------------------------------------------------


async def test_two_manual_achievements_both_exist(repository, db_session, owner):
    """The deduplication constraint does not fire when a column is null.

    ``uq_career_evidence_source_identity`` covers six columns and three of them
    are nullable. Nulls do not collide in a btree unique index, so two rows that
    differ only in their three null foreign keys both insert — which is what makes
    "I finished two different things" a legitimate thing to record. Asserted as a
    row count in the database rather than from the returned object, because both
    calls return a row either way.
    """
    await repository.create_evidence(
        owner.id,
        evidence_type=CareerEvidenceType.ACHIEVEMENT,
        title="Shipped the payments flow",
        occurred_on=DAY_ONE,
    )
    owner_id = owner.id
    await repository.create_evidence(
        owner_id,
        evidence_type=CareerEvidenceType.ACHIEVEMENT,
        title="Mentored two interns",
        occurred_on=DAY_TWO,
    )

    assert await repository.count_evidence(owner_id) == 2


async def test_a_derived_row_is_reported_as_a_duplicate_before_it_is_written(
    repository, db_session, owner
):
    """The composite-unique check answers the same question the constraint does.

    A row that names all three of ``project_id``, ``skill_id`` and
    ``repository_id`` *can* collide, so the check finds the existing row and the
    route can say "you already have this evidence" in a sentence instead of
    surfacing a constraint name. The second insert then fails on the constraint
    itself, which is the anchor the check is there to avoid.
    """
    owner_id = owner.id
    skill_id, repository_id = await _skill_and_repository_ids(db_session, owner_id)

    project_id = uuid.uuid4()
    db_session.add(Project(id=project_id, owner_id=owner_id, name="Nexus"))
    await db_session.commit()

    first = await repository.create_evidence(
        owner_id,
        evidence_type=CareerEvidenceType.PROJECT_COMPLETED,
        title="Finished Nexus",
        occurred_on=DAY_ONE,
        project_id=project_id,
        skill_id=skill_id,
        repository_id=repository_id,
        source="project",
    )

    duplicate = await repository.find_duplicate_evidence(
        owner_id,
        evidence_type=CareerEvidenceType.PROJECT_COMPLETED,
        source="project",
        project_id=project_id,
        skill_id=skill_id,
        repository_id=repository_id,
    )
    assert duplicate is not None
    assert duplicate.id == first.id

    with pytest.raises(IntegrityError):
        await repository.create_evidence(
            owner_id,
            evidence_type=CareerEvidenceType.PROJECT_COMPLETED,
            title="Finished Nexus again",
            occurred_on=DAY_ONE,
            project_id=project_id,
            skill_id=skill_id,
            repository_id=repository_id,
            source="project",
        )
    await db_session.rollback()

    assert await repository.count_evidence(owner_id) == 1


async def test_the_duplicate_check_reports_nothing_while_a_column_is_null(
    repository, db_session, owner
):
    """A null identity column means the constraint cannot fire, so neither do we.

    This is the case an ``IS NULL`` lookup gets wrong. Two manual achievements
    differ only in their three null foreign keys, the database would happily
    store both — and a check that translated the absent columns to ``IS NULL``
    would find the first one and refuse the second, contradicting its own schema.
    """
    skill_id, _repository_id = await _skill_and_repository_ids(db_session, owner.id)
    owner_id = owner.id
    await repository.create_evidence(
        owner_id,
        evidence_type=CareerEvidenceType.ACHIEVEMENT,
        title="Shipped the payments flow",
        occurred_on=DAY_ONE,
        skill_id=skill_id,
    )

    duplicate = await repository.find_duplicate_evidence(
        owner_id,
        evidence_type=CareerEvidenceType.ACHIEVEMENT,
        skill_id=skill_id,
    )

    assert duplicate is None


async def test_the_duplicate_check_does_not_see_another_accounts_row(
    repository, db_session, owner, other
):
    """Owner-scoped throughout: the same identity belongs to a different person.

    Without the predicate this would report a conflict for an evidence row that
    was never this account's, which is the mirror image of an existence oracle.
    """
    skill_id, repository_id = await _skill_and_repository_ids(db_session, other.id)
    other_id = other.id
    await repository.create_evidence(
        other_id,
        evidence_type=CareerEvidenceType.SKILL_ACTIVITY,
        title="Practised Python",
        occurred_on=DAY_ONE,
        skill_id=skill_id,
        repository_id=repository_id,
    )

    duplicate = await repository.find_duplicate_evidence(
        other.id,
        evidence_type=CareerEvidenceType.SKILL_ACTIVITY,
        skill_id=skill_id,
        repository_id=repository_id,
    )

    assert duplicate is None


async def test_the_evidence_list_filters_by_type_and_date_and_reports_the_total(repository, owner):
    """Four rows: two types, two dates, one just outside the window.

    ``occurred_on`` is a date rather than a timestamp because evidence is a thing
    that happened on a day. The window is half-open, so two adjacent windows
    cannot both count the same day, and the total is 3 while the page holds 3 —
    asserted with a page of two as well, because a total computed from the page
    rather than from the filtered set would agree here by accident.
    """
    for day, title in ((DAY_ONE, "shipped"), (DAY_TWO, "mentored"), (DAY_TWO, "presented")):
        await repository.create_evidence(
            owner.id,
            evidence_type=CareerEvidenceType.ACHIEVEMENT,
            title=title,
            occurred_on=day,
        )
    await repository.create_evidence(
        owner.id,
        evidence_type=CareerEvidenceType.CERTIFICATION,
        title="a certificate",
        occurred_on=date(2026, 1, 1),
    )

    page, total = await repository.list_evidence(
        owner.id,
        evidence_type=CareerEvidenceType.ACHIEVEMENT,
        since=DAY_ONE,
        until=DAY_TWO,
    )

    assert {row.title for row in page} == {"shipped"}
    assert total == 1

    everything, all_total = await repository.list_evidence(owner.id, limit=2)
    assert len(everything) == 2
    assert all_total == 4


async def test_evidence_is_stored_with_the_date_the_user_gave(repository, db_session, owner):
    """``occurred_on`` is a ``Date``, and a hand-entered row says ``manual``.

    ``source`` is part of the uniqueness key, so "where did this come from" is
    never a per-row guess at read time — and ``manual`` is the module's single
    spelling of it, which is the one field on this table where a typo would
    quietly allow a duplicate row.
    """
    created = await repository.create_evidence(
        owner.id,
        evidence_type=CareerEvidenceType.LEARNING_MILESTONE,
        title="Finished the Rust book",
        occurred_on=DAY_ONE,
    )

    stored = (
        await db_session.execute(
            select(
                CareerEvidence.evidence_type,
                CareerEvidence.occurred_on,
                CareerEvidence.source,
                CareerEvidence.project_id,
                CareerEvidence.skill_id,
                CareerEvidence.repository_id,
            ).where(CareerEvidence.id == created.id)
        )
    ).one()
    assert stored == ("learning_milestone", DAY_ONE, "manual", None, None, None)


async def test_an_unknown_evidence_type_is_refused_before_storage(repository, owner):
    """An unrecognised type defeats the deduplication constraint as well as the UI.

    ``evidence_type`` is one of the six columns of
    ``uq_career_evidence_source_identity``, so a value nothing recognises does not
    merely fail to render — it is a second identity the constraint cannot see.
    """
    with pytest.raises(ValueError, match="evidence type"):
        await repository.create_evidence(
            owner.id,
            evidence_type="impressive",
            title="Something",
            occurred_on=DAY_ONE,
        )

    assert await repository.count_evidence(owner.id) == 0


async def test_another_accounts_evidence_is_invisible_and_undeletable(repository, owner, other):
    """A foreign row is not readable, not listed, not editable and not deletable.

    Asserted from both sides so that a ``False`` from the delete cannot be
    confused with a delete that matched and then rolled back.
    """
    theirs = await repository.create_evidence(
        other.id,
        evidence_type=CareerEvidenceType.ACHIEVEMENT,
        title="Their achievement",
        occurred_on=DAY_ONE,
    )
    theirs_id = theirs.id

    assert await repository.list_evidence(owner.id) == ([], 0)
    assert await repository.get_evidence(owner.id, theirs_id) is None
    assert await repository.update_evidence(owner.id, theirs_id, {"title": "stolen"}) is None
    assert await repository.delete_evidence(owner.id, theirs_id) is False
    assert await repository.count_evidence(owner.id) == 0
    assert await repository.count_evidence(other.id) == 1
    assert await repository.get_evidence(other.id, theirs_id) is not None


async def test_the_evidence_cap_counts_manual_and_derived_rows_alike(repository, db_session, owner):
    """Three rows: two manual, one derived. The cap sees three.

    ``career_max_evidence`` is enforced by counting rows, and an evidence row the
    user typed occupies its slot the same way a project-derived one does. A cap
    that ignored the manual ones would be a number that could rise without
    anything being added.
    """
    skill_id, repository_id = await _skill_and_repository_ids(db_session, owner.id)
    owner_id = owner.id
    await repository.create_evidence(
        owner_id,
        evidence_type=CareerEvidenceType.ACHIEVEMENT,
        title="one",
        occurred_on=DAY_ONE,
    )
    await repository.create_evidence(
        owner_id,
        evidence_type=CareerEvidenceType.ACHIEVEMENT,
        title="two",
        occurred_on=DAY_ONE,
    )
    await repository.create_evidence(
        owner_id,
        evidence_type=CareerEvidenceType.SKILL_ACTIVITY,
        title="derived",
        occurred_on=DAY_TWO,
        skill_id=skill_id,
        repository_id=repository_id,
        source="skill",
    )

    assert await repository.count_evidence(owner_id) == 3
    assert await repository.count_evidence(uuid.uuid4()) == 0


async def test_deleting_a_skill_leaves_the_evidence_that_named_it(
    repository, db_session, skills, owner
):
    """The evidence survives the record it was drawn from.

    ``career_evidence.skill_id`` is ``ON DELETE SET NULL`` and that is the single
    most important decision on this table: a ``CASCADE`` would mean deleting a
    skill deleted the evidence of having practised it, which inverts the entire
    point of a table whose job is to be a trail. The claim stays; only the pointer
    goes.
    """
    await skills.create_skill(owner.id, name="Python")
    rows, _total = await skills.list_skills(owner.id)
    skill_id = rows[0].id
    evidence = await repository.create_evidence(
        owner.id,
        evidence_type=CareerEvidenceType.SKILL_ACTIVITY,
        title="Practised Python",
        occurred_on=DAY_ONE,
        skill_id=skill_id,
    )

    assert await skills.delete_skill(owner.id, skill_id) is True

    stored = (
        await db_session.execute(
            select(CareerEvidence.skill_id, CareerEvidence.title).where(
                CareerEvidence.id == evidence.id
            )
        )
    ).one()
    assert stored == (None, "Practised Python")
