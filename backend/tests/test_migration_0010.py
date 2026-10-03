"""Regression tests for migration ``0010``: three rules the schema used to only claim.

``0009`` shipped three statements about itself that its own DDL did not enforce.
Each is pinned here against a live PostgreSQL at the revision head, because the
failure mode of all three was that they *looked* right — the constraint was
correctly named, the foreign key existed, the query was ordered — and only a write
or a query plan reveals that the rule was not there.

The three defects, and what each test would have caught
------------------------------------------------------

1. **Deduplication that did not deduplicate.**
   ``uq_career_evidence_source_identity`` was a table-level ``UNIQUE`` constraint.
   PostgreSQL's btree unique index is ``NULLS DISTINCT`` by default, so a null
   equals nothing: two evidence rows for the same project — ``project_id`` set,
   ``skill_id`` and ``repository_id`` null — did not collide and both inserted.
   The constraint fired only when all three foreign keys were non-null, which is
   the one case in which nothing needed it. The fix is a *partial* unique index
   with ``NULLS NOT DISTINCT``, and
   :func:`test_a_second_evidence_row_derived_from_the_same_source_is_refused`
   attempts the insert rather than reading the catalogue.

2. **A cascade that destroyed the record.**
   ``learning_activities.skill_id`` was ``ON DELETE CASCADE``, so deleting one
   skill silently removed every activity recorded against it — invisibly, because
   the table is append-only and has no ``updated_at`` in which to record the loss.
   The fix is ``SET NULL``, which is what
   :attr:`~app.models.learning.LearningActivity.goal_id` already did, and
   :func:`test_deleting_a_skill_leaves_the_activities_recorded_against_it_standing`
   deletes the skill and counts what is left.

3. **A missing index on the feed's hot path.**
   :meth:`app.repositories.activity.ActivityRepository.list_for_user` orders every
   page by ``created_at DESC, id DESC`` over ``user_id``, and ``activity_events``
   had only a bare ``(user_id)`` index — which can find the rows but cannot order
   them.
   :func:`test_the_activity_feed_read_is_served_by_the_owner_created_index` asserts
   the index ``0010`` added is present, is shaped the way that read needs it, and
   is one the planner will actually reach for on the feed's own statement.

House style, deliberately
-------------------------
Follows ``tests/test_career_service.py``:

* ``pytestmark = pytest.mark.integration`` — every test here needs the live
  PostgreSQL the suite truncates between tests, and the first three are about what
  the *database* accepts rather than about what a service does with it.
* Rows are written through the ORM and read back through **explicit column
  tuples**. The duplicate insert happens in the same session that later counts the
  rows, so an entity read would hand back whatever the identity map had cached.
* Every expected figure is derived in the test's own docstring rather than
  recorded from a run, and dates are pinned to whole days so the arithmetic holds
  at any hour.
* Evidence rows are inserted **directly**, bypassing
  ``CareerIntelligenceService``. The claim under test is what the schema refuses;
  a service that pre-checked the duplicate in Python would pass every one of
  these tests with the broken constraint still in place.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import ActivityLog
from app.models.career import DEFAULT_CAREER_EVIDENCE_SOURCE, CareerEvidence
from app.models.developer import GitRepository
from app.models.enums import CareerEvidenceType, LearningActivityType
from app.models.learning import LearningActivity, Skill
from app.models.project import Project
from app.models.user import User
from tests.analytics_fixtures import register_user

pytestmark = pytest.mark.integration

#: The day a derived evidence row is dated on. A ``career_evidence`` row is a
#: claim about something that happened *on a day*, and pinning the date is what
#: makes the two rows of a duplicate pair differ only where the test means them to.
DAY = date(2026, 3, 17)
#: The instant a recorded activity is stamped with, for the same reason: the
#: activity timeline is windowed, and a wall-clock "now" would put a seeded row in
#: or out of any window depending on the hour the suite happened to run.
ANCHOR = datetime(2026, 3, 17, 19, 30, tzinfo=UTC)

#: The three achievements the manual-evidence test records. Three is the number
#: the docstring derives; the list is the fixture it derives them from.
ACHIEVEMENTS = ("Rewrote the import path", "Ran the 2019 half marathon", "Mentored two interns")


# ---------------------------------------------------------------------------
# Fixtures, written straight through the ORM
# ---------------------------------------------------------------------------


async def _owner(session: AsyncSession, username: str = "ada") -> User:
    """One account.

    Direct, because that is the whole of what a fixture needs here: no service
    wiring, no HTTP, and no activity events that would move a count in another
    test.
    """
    return await register_user(session, username=username)


async def _project(session: AsyncSession, owner_id: uuid.UUID, name: str = "atlas") -> Project:
    """One project for the owner to have derived evidence from."""
    project = Project(owner_id=owner_id, name=name)
    session.add(project)
    await session.commit()
    return project


async def _skill(session: AsyncSession, owner_id: uuid.UUID, name: str = "Rust") -> Skill:
    """One tracked skill."""
    skill = Skill(user_id=owner_id, name=name)
    session.add(skill)
    await session.commit()
    return skill


async def _repository(
    session: AsyncSession, owner_id: uuid.UUID, name: str = "engine"
) -> GitRepository:
    """One registered repository, never scanned.

    ``last_scanned_at`` stays null, which is irrelevant here: the deduplication
    key is the repository's *identity*, not anything derived from having read it.
    """
    repository = GitRepository(
        user_id=owner_id,
        name=name,
        local_path=f"/srv/nexus-fixtures/{owner_id}/{name}",
    )
    session.add(repository)
    await session.commit()
    return repository


async def _activity(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    skill_id: uuid.UUID | None,
    title: str,
) -> LearningActivity:
    """One recorded activity, optionally naming a skill."""
    activity = LearningActivity(
        user_id=owner_id,
        skill_id=skill_id,
        activity_type=LearningActivityType.STUDY_SESSION.value,
        title=title,
        occurred_at=ANCHOR,
        duration_minutes=45,
    )
    session.add(activity)
    await session.commit()
    return activity


def _evidence(
    owner_id: uuid.UUID,
    *,
    title: str,
    occurred_on: date = DAY,
    evidence_type: CareerEvidenceType | str = CareerEvidenceType.PROJECT_COMPLETED,
    source: str = DEFAULT_CAREER_EVIDENCE_SOURCE,
    project_id: uuid.UUID | None = None,
    skill_id: uuid.UUID | None = None,
    repository_id: uuid.UUID | None = None,
) -> CareerEvidence:
    """An unsaved evidence row with every column of the dedup key set explicitly.

    Every field of the key is passed rather than defaulted, so a duplicate insert
    below differs from its twin in the ``id`` and ``created_at`` the database
    assigns and in nothing else. That is the only way the assertion can be about
    the constraint rather than about some other column happening to differ.
    """
    return CareerEvidence(
        user_id=owner_id,
        evidence_type=CareerEvidenceType(evidence_type).value,
        title=title,
        occurred_on=occurred_on,
        source=source,
        project_id=project_id,
        skill_id=skill_id,
        repository_id=repository_id,
    )


async def _linked_id(db_session: AsyncSession, owner: User, linked_column: str) -> uuid.UUID:
    """The id of whichever source ``linked_column`` names, created on demand."""
    if linked_column == "project_id":
        return (await _project(db_session, owner.id)).id
    if linked_column == "skill_id":
        return (await _skill(db_session, owner.id)).id
    return (await _repository(db_session, owner.id)).id


# ---------------------------------------------------------------------------
# Defect 1: deduplication that did not deduplicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "linked_column",
    ["project_id", "skill_id", "repository_id"],
    ids=["from-a-project", "from-a-skill", "from-a-repository"],
)
async def test_a_second_evidence_row_derived_from_the_same_source_is_refused(
    db_session: AsyncSession, linked_column: str
):
    """One derived row is stored; the second, identical one is refused by the database.

    The figures: **one** evidence row naming a project (or a skill, or a
    repository) is inserted and committed. A second row is then built with the
    same ``user_id``, the same ``evidence_type``, the same ``source``, the same
    ``title``, the same ``occurred_on`` and the same linked id — differing only in
    the ``id`` and ``created_at`` the database assigns. The assertion is that the
    second ``INSERT`` raises :exc:`~sqlalchemy.exc.IntegrityError` and that
    **one** row is stored afterwards.

    Before ``0010`` all three cases failed, which is why the test is parameterised
    over them. A project-derived row has ``project_id`` set and the other two
    foreign keys null, so under the old ``UNIQUE`` constraint it collided with
    nothing: a null compared unequal to a null, the two tuples were not equal, and
    both rows survived. The same held for a skill-derived row and for a
    repository-derived row. Those three are precisely the cases the old constraint
    could not see, because it fired only when all three foreign keys were
    non-null — the one case in which there was nothing to deduplicate.
    """
    owner = await _owner(db_session)
    # Captured rather than read off `owner` later: the rollback below expires
    # every attribute on every instance this session holds, and re-reading
    # `owner.id` afterwards would trigger a lazy load from inside the assertion
    # rather than from a query the test is making.
    owner_id = owner.id
    linked_id = await _linked_id(db_session, owner, linked_column)

    first = _evidence(
        owner_id,
        title="Shipped the migration",
        source="project",
        **{linked_column: linked_id},
    )
    db_session.add(first)
    await db_session.commit()
    first_id = first.id

    duplicate = _evidence(
        owner_id,
        title="Shipped the migration",
        source="project",
        **{linked_column: linked_id},
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    stored = (
        await db_session.execute(
            select(CareerEvidence.id, CareerEvidence.title, CareerEvidence.source).where(
                CareerEvidence.user_id == owner_id
            )
        )
    ).all()
    assert stored == [(first_id, "Shipped the migration", "project")]


async def test_several_manual_achievements_with_no_source_still_coexist(db_session: AsyncSession):
    """Three things one person did, none of them derived, and all three are kept.

    The figures: **three** achievements, each with ``project_id``, ``skill_id`` and
    ``repository_id`` all null and each carrying a different ``title`` and a
    different ``occurred_on``, are inserted into an empty table; **three** rows are
    read back, oldest first, with their own titles and dates intact.

    This is the case the fix had to avoid breaking. The obvious alternative to the
    partial index is a blanket ``UNIQUE NULLS NOT DISTINCT`` over the same six
    columns, and it *would* have refused the second of these three rows — inside
    such an index nulls are equal to each other, and all three foreign keys are
    null on both sides. That would have fixed the first defect by creating the
    second: a user recording two separate achievements is not a duplicate, and
    refusing them is not a decision this database is entitled to make.
    """
    owner = await _owner(db_session)

    for offset, title in enumerate(ACHIEVEMENTS):
        db_session.add(
            _evidence(
                owner.id,
                title=title,
                occurred_on=DAY + timedelta(days=offset),
                evidence_type=CareerEvidenceType.ACHIEVEMENT,
            )
        )
    await db_session.commit()

    stored = (
        await db_session.execute(
            select(CareerEvidence.title, CareerEvidence.occurred_on)
            .where(CareerEvidence.user_id == owner.id)
            .order_by(CareerEvidence.occurred_on.asc())
        )
    ).all()
    assert stored == [
        (title, DAY + timedelta(days=offset)) for offset, title in enumerate(ACHIEVEMENTS)
    ]


async def test_a_manual_row_and_a_derived_row_about_the_same_project_are_both_kept(
    db_session: AsyncSession,
):
    """``source`` is part of the identity, so the two claims about one project do not collide.

    The figures: **two** rows naming the *same* project — one written by the user
    (``source='manual'``) and one derived by NEXUS (``source='project'``) — are
    inserted into an empty table, and **two** rows are read back.

    They differ in exactly one column of the key, and that column is the only
    thing standing between them: a claim NEXUS derived from a project and a claim
    the user made about the same project are different statements, and a narrower
    index that dropped ``source`` would have forbidden the second of them as a
    duplicate of the first.
    """
    owner = await _owner(db_session)
    project = await _project(db_session, owner.id)

    for source in ("manual", "project"):
        db_session.add(
            _evidence(
                owner.id,
                title=f"Completed {project.name}",
                source=source,
                project_id=project.id,
            )
        )
    await db_session.commit()

    stored = (
        (
            await db_session.execute(
                select(CareerEvidence.source)
                .where(CareerEvidence.user_id == owner.id)
                .order_by(CareerEvidence.source.asc())
            )
        )
        .scalars()
        .all()
    )
    assert stored == ["manual", "project"]


# ---------------------------------------------------------------------------
# Defect 2: a cascade that destroyed the record
# ---------------------------------------------------------------------------


async def test_deleting_a_skill_leaves_the_activities_recorded_against_it_standing(
    db_session: AsyncSession,
):
    """Three activities against one skill; delete the skill; all three remain.

    The figures: **three** activities are recorded against **one** skill, so the
    account's activity total is **three**. The skill is deleted. Afterwards the
    skill row is gone (**zero** rows for that id), the activity total is still
    **three** rather than zero, and all three rows read back with ``skill_id``
    **null** and ``user_id`` still the owner.

    Before ``0010`` that total was **zero**. ``skill_id`` was ``ON DELETE
    CASCADE``, so the ``DELETE`` on ``skills`` took the three activities with it in
    the same statement, and nothing recorded that it had: ``learning_activities``
    deliberately has no ``updated_at``, because the table asserts its rows are
    never revised. The loss of three recorded sessions therefore left no mark
    anywhere at all. The surviving rows carry a null subject rather than nothing
    at all, which is the state the column was already nullable for — a session
    recorded before its skill row existed reads identically to every consumer.
    """
    owner = await _owner(db_session)
    skill = await _skill(db_session, owner.id)
    recorded = [
        await _activity(db_session, owner.id, skill_id=skill.id, title=f"Session {number}")
        for number in range(3)
    ]

    before = await db_session.scalar(
        select(func.count())
        .select_from(LearningActivity)
        .where(LearningActivity.user_id == owner.id)
    )
    assert before == 3

    await db_session.execute(delete(Skill).where(Skill.id == skill.id))
    await db_session.commit()

    skills_left = await db_session.scalar(
        select(func.count()).select_from(Skill).where(Skill.id == skill.id)
    )
    assert skills_left == 0

    after = (
        await db_session.execute(
            select(LearningActivity.id, LearningActivity.skill_id, LearningActivity.user_id)
            .where(LearningActivity.user_id == owner.id)
            .order_by(LearningActivity.title.asc())
        )
    ).all()
    assert after == [(activity.id, None, owner.id) for activity in recorded]


# ---------------------------------------------------------------------------
# Defect 3: a missing index on the feed's hot path
# ---------------------------------------------------------------------------


async def test_the_activity_feed_read_is_served_by_the_owner_created_index(engine):
    """The feed's ordering has an index behind it, and the planner will use it.

    :meth:`app.repositories.activity.ActivityRepository.list_for_user` orders every
    page of the feed by ``created_at DESC, id DESC`` filtered on ``user_id``.
    Before ``0010`` the only index on that leading column was ``(user_id)``, which
    can select the account's rows but cannot return them in order, so PostgreSQL
    sorted the entire history to render the first twenty. Nothing prunes
    ``activity_events``: it is append-only and every row is kept, so that sort was
    measured at **0.19 ms**, **2.8 ms** and **13.3 ms** for 1k, 20k and 100k
    events.

    Two claims are asserted, each answering a different way the index could be
    present and useless. First, it **exists** in the live schema as a plain btree
    over ``(user_id, created_at)`` in that order — the equality column has to lead
    and the sort column has to be second, and it is *not* unique, because two
    events may share an instant. Second, the planner **reaches for it** on the
    feed's own statement, compiled from the same ORM expression the repository
    uses. Sequential scans are disabled for that probe because the table is empty
    in this session and the cost model would otherwise answer a question about the
    fixture rather than about the schema: the claim is that an index scan is
    *available* for this ordering, not that it is cheapest on a table of no rows.
    """
    async with engine.connect() as connection:
        definitions = (
            (
                await connection.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes"
                        " WHERE schemaname = current_schema()"
                        " AND tablename = 'activity_events'"
                    )
                )
            )
            .scalars()
            .all()
        )
        definition = next(d for d in definitions if "ix_activity_events_owner_created" in d)
        assert definition.startswith("CREATE INDEX ")
        assert "USING btree" in definition
        assert "(user_id, created_at)" in definition

        # The statement `list_for_user` builds, projection trimmed to the columns
        # it selects. Built from the model rather than pasted as SQL so a change to
        # the repository's ordering shows up here as a failing name.
        page = (
            select(ActivityLog.id, ActivityLog.user_id, ActivityLog.created_at)
            .where(ActivityLog.user_id == uuid.UUID(int=1))
            .order_by(ActivityLog.created_at.desc(), ActivityLog.id.desc())
            .limit(20)
        )
        statement = text(
            "EXPLAIN (FORMAT JSON) "
            + page.compile(
                dialect=connection.dialect,
                compile_kwargs={"literal_binds": True},
            ).string
        )
        await connection.execute(text("SET LOCAL enable_seqscan = off"))
        await connection.execute(text("SET LOCAL enable_bitmapscan = off"))
        plan = (await connection.execute(statement)).scalar_one()
        await connection.rollback()

    rendered = str(plan)
    assert "ix_activity_events_owner_created" in rendered, rendered
    # A bare `(user_id)` index would satisfy the plan check on its own, so the
    # assertion is on the composite by name and not merely on "an index was used".
    assert "ix_activity_events_user_id" not in rendered, rendered
