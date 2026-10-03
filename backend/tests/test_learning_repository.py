"""LearningRepository against the real test database.

Four properties are worth pinning, and none of them is visible from reading the
code:

**A goal's claims are the user's and nothing fills them in.** A goal arrives at
``not_started`` with ``progress`` 0 and no completion stamp, because "created
during planning" demonstrably has not started and defaulting it forward would
claim an activity nobody recorded. Completion then writes ``status``,
``completed_at`` and ``progress`` in one statement, and *un*-completing a goal
clears the stamp — because ``ck_learning_goals_completed_has_terminal_status``
would otherwise turn a legitimate PATCH into a constraint error and leave the
route with two columns to remember to move together.

**A skill's evidence is NEXUS's observation and a PATCH cannot touch it.**
``evidence_count`` and ``last_activity_at`` are absent from the skill edit set on
purpose. If an edit could move them, a skill could claim study sessions that were
never recorded and the gap service would quote them as the evidence behind a
level. ``record_skill_evidence`` is the only writer, and it moves recency with
``greatest(...)`` so back-filling last week cannot make today's activity look
older than it is.

**A real zero is not an absence.** ``activity_totals`` coalesces the *count* to
0 because a count of rows is always computable, and leaves ``measured_minutes``
as ``None`` because nothing in the window was timed. ``activity_counts_by_skill``
returns an entry for every skill it was asked about, including the ones with
nothing recorded, because the window was searched and found empty. The
distinction the phase cares about — measured zero versus never measured — is only
survivable because neither read is cached.

**Nothing is visible across accounts.** Every read filters on ``user_id`` in its
``WHERE`` clause, and a foreign id is answered with ``None`` or ``False`` rather
than a 403 — a 403 would confirm the row exists and turn the endpoint into an
existence oracle. Asserted from both sides: the other account's goal is
invisible *and* undeletable, and its activities are not reachable by listing or
by any window total.

Instants are explicit UTC values, never ``now()``: the windowed reads bucket
days, and a suite whose expected counts move with the wall clock fails at
midnight instead of failing for a reason. The one place the database clock *is*
asserted — a completion stamp the repository wrote with ``func.now()`` — is
compared against the same clock read back over the wire.

Rows are read back through explicit column projections rather than through the
ORM wherever the assertion is about what was *stored*: this session is the one
that wrote them, so an entity read would hand back whatever the identity map
cached and the assertions could pass for the wrong reason.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.enums import LearningActivityType, LearningGoalStatus, SkillLevelSource
from app.models.learning import LearningActivity, LearningGoal, Skill
from app.models.project import Project
from app.repositories.learning import LearningRepository
from tests.analytics_fixtures import register_user

pytestmark = pytest.mark.integration

#: The anchor every relative instant counts from. A fixed Monday, so the
#: UTC-day arithmetic in the totals test is not sitting on a boundary by
#: accident.
ANCHOR = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture
def repository(db_session) -> LearningRepository:
    return LearningRepository(db_session)


@pytest.fixture
async def owner(db_session):
    """The account most tests write as."""
    return await register_user(db_session, username="ada", email="ada@nexus.test")


@pytest.fixture
async def other(db_session):
    """A second account, for every isolation assertion."""
    return await register_user(db_session, username="grace", email="grace@nexus.test")


async def _now(db_session) -> datetime:
    """The database's own clock, read back rather than guessed.

    Used to compare against a stamp the repository wrote with ``func.now()``.
    ``datetime.now()`` would be the wrong comparison: the value under test was
    produced by PostgreSQL, so the assertion has to come from the same clock.
    """
    return await db_session.scalar(select(func.now()))


async def _goal_columns(db_session, goal_id: uuid.UUID) -> tuple:
    """Read one goal's claim columns straight out of the database."""
    return (
        await db_session.execute(
            select(
                LearningGoal.status,
                LearningGoal.priority,
                LearningGoal.progress,
                LearningGoal.completed_at,
                LearningGoal.target_date,
            ).where(LearningGoal.id == goal_id)
        )
    ).one()


async def _skill_columns(db_session, skill_id: uuid.UUID) -> tuple:
    """Read one skill's level, provenance and evidence columns directly."""
    return (
        await db_session.execute(
            select(
                Skill.current_level,
                Skill.target_level,
                Skill.level_source,
                Skill.confidence,
                Skill.evidence_count,
                Skill.last_activity_at,
            ).where(Skill.id == skill_id)
        )
    ).one()


async def _activity_columns(db_session, activity_id: uuid.UUID) -> tuple:
    """Read one activity's stored columns straight out of the database.

    Read from the table rather than off the returned object for the same reason
    the two helpers above do it, and it matters more here: the session runs with
    ``expire_on_commit=False``, so an instance the test already holds keeps the
    attribute values it was loaded with, and a later ``SELECT`` does not
    overwrite them. A database-side ``ON DELETE SET NULL`` is invisible on such
    an instance — only the stored row says what the constraint did.
    """
    return (
        await db_session.execute(
            select(
                LearningActivity.title,
                LearningActivity.duration_minutes,
                LearningActivity.occurred_at,
                LearningActivity.skill_id,
            ).where(LearningActivity.id == activity_id)
        )
    ).one()


async def _make_skill(repository, owner, name: str, **overrides) -> Skill:
    """Create one skill for the owner, with the columns the tests care about."""
    return await repository.create_skill(owner.id, name=name, **overrides)


# ----------------------------------------------------------------------
# Goals: what a claim looks like before anyone moves it
# ----------------------------------------------------------------------


async def test_a_new_goal_claims_no_progress_and_no_completion(repository, db_session, owner):
    """A goal is created unfinished, and every column says so.

    ``not_started`` is the honest default rather than ``in_progress``: somebody
    who wrote a goal during planning has demonstrably not started it, and
    defaulting it forward would assert an activity nobody recorded. Progress is
    0 — a real measurement of a goal with no work against it yet — and
    ``completed_at`` is null, which is a different fact from "completed on an
    unknown day".
    """
    created = await repository.create_goal(owner.id, title="Learn Rust")

    assert isinstance(created, LearningGoal)
    assert created.user_id == owner.id
    assert created.title == "Learn Rust"
    status, priority, progress, completed_at, target_date = await _goal_columns(
        db_session, created.id
    )
    assert status == LearningGoalStatus.NOT_STARTED.value
    assert priority == "medium"
    assert progress == 0
    assert completed_at is None
    assert target_date is None
    assert created.created_at.tzinfo is not None
    assert created.updated_at.tzinfo is not None


async def test_completion_writes_status_stamp_and_progress_in_one_statement(
    repository, db_session, owner
):
    """``complete_goal`` moves three columns that the constraint holds together.

    The check constraint says a completion stamp without a terminal status is a
    contradiction, so a caller doing this in two requests would have a window in
    which the row claimed neither or both. Progress goes to 100 because a
    finished goal reporting 40% is not a state a user can argue with — and it is
    derived from the *completion*, not from anything about the person.

    ``completed_at`` comes from the database clock, so it is compared against the
    database clock rather than against ``datetime.now()``.
    """
    created = await repository.create_goal(owner.id, title="Learn Rust", progress=30)

    before = await _now(db_session)
    completed = await repository.complete_goal(owner.id, created.id)
    after = await _now(db_session)

    assert completed is not None
    status, _priority, progress, completed_at, _date = await _goal_columns(db_session, created.id)
    assert status == LearningGoalStatus.COMPLETED.value
    assert progress == 100
    assert completed_at is not None
    assert completed_at.tzinfo is not None
    assert before <= completed_at <= after


async def test_completion_accepts_the_users_own_instant(repository, db_session, owner):
    """A back-dated completion is stored as given, not as the request's clock.

    People finish things they forgot to log on the day. Overwriting that with
    ``now()`` would be the same small invention the phase forbids everywhere
    else — the row would claim the user finished today when they said last
    Tuesday.
    """
    created = await repository.create_goal(owner.id, title="Learn Rust")
    stamped = ANCHOR - timedelta(days=3)

    completed = await repository.complete_goal(owner.id, created.id, completed_at=stamped)

    assert completed is not None
    _status, _priority, _progress, completed_at, _date = await _goal_columns(db_session, created.id)
    assert completed_at == stamped


async def test_reopening_a_completed_goal_clears_the_completion_stamp(
    repository, db_session, owner
):
    """Moving a goal out of ``completed`` must take ``completed_at`` with it.

    ``ck_learning_goals_completed_has_terminal_status`` refuses a stamp without a
    terminal status, so a bare ``status`` PATCH would fail on the constraint and
    the user would be told their own goal could not be reopened. The repository
    holds the two together instead of making the caller remember.
    """
    created = await repository.create_goal(owner.id, title="Learn Rust")
    await repository.complete_goal(owner.id, created.id)

    reopened = await repository.update_goal(
        owner.id, created.id, {"status": LearningGoalStatus.PAUSED}
    )

    assert reopened is not None
    status, _priority, _progress, completed_at, _date = await _goal_columns(db_session, created.id)
    assert status == LearningGoalStatus.PAUSED.value
    assert completed_at is None


async def test_completing_a_foreign_goal_writes_nothing(repository, db_session, owner, other):
    """A 404, not a 403: the completion is owner-scoped and returns ``None``.

    Asserted on the row afterwards as well as on the return value — an
    ``UPDATE`` that filtered nothing would still answer ``None`` if only the
    ``RETURNING`` clause were right, so only the re-read proves nothing leaked.
    """
    theirs = await repository.create_goal(other.id, title="Theirs", progress=10)

    assert await repository.complete_goal(owner.id, theirs.id) is None

    status, _priority, progress, completed_at, _date = await _goal_columns(db_session, theirs.id)
    assert status == LearningGoalStatus.NOT_STARTED.value
    assert progress == 10
    assert completed_at is None


# ----------------------------------------------------------------------
# Goals: listing and filtering
# ----------------------------------------------------------------------


async def test_the_goal_list_is_ordered_by_due_date_with_the_undated_last(repository, owner):
    """Three goals: two dated, one not, paged two at a time.

    Ordered by what is due, because a goal list is a plan and a plan is ordered
    by deadline; the undated goal is last rather than first, because ``NULL``
    sorts low in PostgreSQL and "no deadline" would otherwise lead a page as if it
    were the most urgent thing on it. ``created_at`` then ``id`` break the ties so
    paging is stable. The total is 3 while the page holds 2, which is the whole
    reason the count is computed over the same filtered rows.
    """
    await repository.create_goal(owner.id, title="undated")
    await repository.create_goal(owner.id, title="later", target_date=date(2026, 6, 1))
    await repository.create_goal(owner.id, title="sooner", target_date=date(2026, 4, 1))

    page, total = await repository.list_goals(owner.id, limit=2)
    rest, rest_total = await repository.list_goals(owner.id, limit=2, offset=2)

    assert [row.title for row in page] == ["sooner", "later"]
    assert total == 3
    assert [row.title for row in rest] == ["undated"]
    assert rest_total == 3


async def test_the_goal_filters_narrow_by_status_skill_and_project(repository, db_session, owner):
    """Two goals, one open against a skill and project, one completed.

    Each filter is asserted against a fixture where the *other* row would
    otherwise change the answer: the matching row is the only one that is also
    ``in_progress``, so a filter that silently did nothing would still fail here.

    The project is a real row because ``learning_goals.project_id`` is a foreign
    key — which is the point of it being one: a link to a project that does not
    exist is not representable.
    """
    project = Project(id=uuid.uuid4(), owner_id=owner.id, name="Nexus")
    db_session.add(project)
    await db_session.commit()
    skill = await _make_skill(repository, owner, "Python")

    linked = await repository.create_goal(
        owner.id,
        title="Ship the API",
        status=LearningGoalStatus.IN_PROGRESS,
        target_skill_id=skill.id,
        project_id=project.id,
    )
    await repository.create_goal(owner.id, title="Read a book", status=LearningGoalStatus.COMPLETED)

    by_status, status_total = await repository.list_goals(
        owner.id, status=LearningGoalStatus.IN_PROGRESS
    )
    by_skill, skill_total = await repository.list_goals(owner.id, target_skill_id=skill.id)
    by_project, project_total = await repository.list_goals(owner.id, project_id=project.id)

    assert [row.id for row in by_status] == [linked.id]
    assert status_total == 1
    assert [row.id for row in by_skill] == [linked.id]
    assert skill_total == 1
    assert [row.id for row in by_project] == [linked.id]
    assert project_total == 1


async def test_the_goal_date_window_is_half_open(repository, owner):
    """Three goals due on three consecutive days.

    ``[2026-04-01, 2026-04-03)`` contains the first two and not the third. A
    closed window would count the boundary goal twice across two adjacent
    windows, which for a due-soon chart is the boundary that matters most.
    """
    for day, title in ((1, "one"), (2, "two"), (3, "three")):
        await repository.create_goal(owner.id, title=title, target_date=date(2026, 4, day))

    window, total = await repository.list_goals(
        owner.id, target_after=date(2026, 4, 1), target_before=date(2026, 4, 3)
    )

    assert [row.title for row in window] == ["one", "two"]
    assert total == 2


async def test_the_goal_cap_counts_every_goal_the_user_wrote(repository, owner):
    """Three goals, one archived: the cap sees all three.

    ``learning_max_goals`` is enforced by counting rows, and an archived goal the
    user wants to keep still occupies its slot. A cap that ignored it would be a
    number that could rise without anything being created.
    """
    for title in ("one", "two", "three"):
        await repository.create_goal(owner.id, title=title)
    rows, _total = await repository.list_goals(owner.id)
    await repository.update_goal(owner.id, rows[0].id, {"status": LearningGoalStatus.ARCHIVED})

    assert await repository.count_goals(owner.id) == 3
    assert await repository.count_goals(owner.id, status=LearningGoalStatus.ARCHIVED) == 1
    assert await repository.count_goals(uuid.uuid4()) == 0


async def test_deleting_a_goal_leaves_the_record_of_the_work_behind(repository, owner):
    """Deleting a goal keeps the activities recorded towards it.

    ``learning_activities.goal_id`` is ``ON DELETE SET NULL`` precisely for this:
    a goal the user abandoned must not erase the record that they once worked on
    it, which is exactly the history the skill's evidence count summarises.
    """
    goal = await repository.create_goal(owner.id, title="Learn Rust")
    skill = await _make_skill(repository, owner, "Rust")
    await repository.create_activity(
        owner.id,
        title="Read the book chapter",
        activity_type=LearningActivityType.STUDY_SESSION,
        goal_id=goal.id,
        skill_id=skill.id,
        occurred_at=ANCHOR,
    )

    removed = await repository.delete_goal(owner.id, goal.id)

    assert removed is True
    activities, total = await repository.list_activities(owner.id, skill_id=skill.id)
    assert total == 1
    assert activities[0].goal_id is None


async def test_another_accounts_goals_are_invisible_and_undeletable(repository, owner, other):
    """A foreign goal is invisible, uneditable and undeletable.

    The same ``None``/``False`` an unknown id gets, so the goal routes cannot be
    used to discover which goal ids exist or which belong to whom. The final
    read is done by the *owner* of the row, because a test that only checked
    ``False`` would pass even if the delete had matched and rolled back.
    """
    theirs = await repository.create_goal(other.id, title="Theirs")
    unknown = uuid.uuid4()

    assert await repository.list_goals(owner.id) == ([], 0)
    assert await repository.get_goal(owner.id, theirs.id) is None
    assert await repository.get_goal(owner.id, unknown) is None
    assert await repository.update_goal(owner.id, theirs.id, {"title": "stolen"}) is None
    assert await repository.delete_goal(owner.id, theirs.id) is False
    assert await repository.get_goal(other.id, theirs.id) is not None


async def test_an_unknown_status_or_an_impossible_progress_is_refused(repository, owner):
    """Both are statements about the calling code, and both raise.

    ``status`` is what the open-goals, overdue-goals and completion-rate queries
    all filter on, so a value nothing recognises leaves a goal that counts as
    neither finished nor outstanding — the one place being wrong cannot be
    noticed from the numbers, because the total still adds up. ``progress`` is
    checked here for the same reason ``record_scan_run`` checks its counts:
    a check constraint rejects the row with no mention of which value was wrong.
    """
    with pytest.raises(ValueError, match="goal status"):
        await repository.create_goal(owner.id, title="Bad", status="nearly done")
    with pytest.raises(ValueError, match="progress"):
        await repository.create_goal(owner.id, title="Bad", progress=150)
    with pytest.raises(ValueError, match="goal status"):
        await repository.update_goal(owner.id, uuid.uuid4(), {"status": "abandoned"})

    assert await repository.count_goals(owner.id) == 0


# ----------------------------------------------------------------------
# Skills: the claim and its provenance
# ----------------------------------------------------------------------


async def test_a_new_skill_starts_at_the_users_position_not_an_estimate(
    repository, db_session, owner
):
    """A freshly named skill is a name and nothing else.

    Level 1 aiming at 3, ``user_defined`` and ``confidence`` 0. The confidence
    reads 0 rather than a mid value because it means *nothing to estimate from*,
    not "low but present" — which is exactly why the source is ``user_defined``
    and not ``system_estimate``, and why creating a skill as an estimate would be
    claiming an inference that has not happened. ``last_activity_at`` is null,
    which is a different fact from "recorded, long ago".
    """
    created = await _make_skill(repository, owner, "Python", category="language")

    current, target, source, confidence, evidence, last_seen = await _skill_columns(
        db_session, created.id
    )
    assert current == 1
    assert target == 3
    assert source == SkillLevelSource.USER_DEFINED.value
    assert confidence == 0
    assert evidence == 0
    assert last_seen is None
    assert created.category == "language"
    assert created.description is None


async def test_a_level_outside_the_scale_is_refused_before_storage(repository, owner):
    """1-5 in, and the refusal names the field.

    The check constraints enforce the same range in the database, because a
    background job writing 150 should not get further than a validated request.
    This is the friendlier version of it: it says *which* value was wrong.
    """
    with pytest.raises(ValueError, match="current_level"):
        await _make_skill(repository, owner, "Python", current_level=6)
    with pytest.raises(ValueError, match="target_level"):
        await _make_skill(repository, owner, "Python", target_level=0)
    with pytest.raises(ValueError, match="confidence"):
        await _make_skill(repository, owner, "Python", confidence=101)
    with pytest.raises(ValueError, match="level source"):
        await _make_skill(repository, owner, "Python", level_source="vibes")

    assert await repository.count_skills(owner.id) == 0


async def test_one_skill_name_may_be_tracked_twice_by_two_accounts(
    repository, db_session, owner, other
):
    """``uq_skills_owner_name`` is per account, not global.

    Two people may each track "Python" — that is the entire point of a per-account
    notebook, and a global index would make the second person's skill a conflict
    about a vocabulary they do not share. The same owner tracking it twice is the
    conflict the constraint exists for: two rows would hold two ``current_level``
    values that could disagree, and every gap read would have to pick one.

    Both owner ids are read *before* the rollback: a rollback expires every
    instance the session holds, and reading ``owner.id`` afterwards would try to
    refresh it outside a greenlet — a failure with nothing to do with the
    constraint under test.
    """
    owner_id, other_id = owner.id, other.id
    await _make_skill(repository, owner, "Python")
    await _make_skill(repository, other, "Python")

    with pytest.raises(IntegrityError):
        await _make_skill(repository, owner, "Python")
    await db_session.rollback()

    assert await repository.count_skills(owner_id) == 1
    assert await repository.count_skills(other_id) == 1


async def test_the_owner_scoped_name_lookup_does_not_invent_a_conflict(repository, owner, other):
    """The duplicate-name lookup is scoped, or it refuses a row that is fine.

    The skill route asks this before it inserts so it can say "you are already
    tracking Python" instead of raising a constraint error. A lookup that ignored
    the owner would tell the second account they had a duplicate they do not
    have.
    """
    await _make_skill(repository, other, "Python")

    assert await repository.get_skill_by_name(owner.id, "Python") is None
    assert await repository.get_skill_by_name(other.id, "Python") is not None


async def test_the_skill_list_filters_by_category_and_reports_the_unpaged_total(repository, owner):
    """Three skills, two of them languages.

    Ordered by name because a skill list with no order is a set, and the category
    filter is an equality filter rather than a validation: ``language``,
    ``framework``, ``domain`` and ``practice`` are suggestions, not a closed set,
    and a user who calls something "embedded" is not wrong.
    """
    for name, category in (
        ("Rust", "language"),
        ("Django", "framework"),
        ("SQL", "language"),
    ):
        await _make_skill(repository, owner, name, category=category)

    page, total = await repository.list_skills(owner.id, category="language")

    assert [row.name for row in page] == ["Rust", "SQL"]
    assert total == 2
    assert await repository.count_skills(owner.id) == 3
    assert await repository.count_skills(uuid.uuid4()) == 0


async def test_a_skill_edit_cannot_move_the_evidence_it_rests_on(repository, owner):
    """``evidence_count`` and ``last_activity_at`` are not in the edit set.

    They are observations, not declarations. If a PATCH could move them, a skill
    could claim study sessions that were never recorded, and the gap service
    would quote them as the evidence behind a level — which is the one claim in
    this phase that is not the user's to make.
    """
    created = await _make_skill(repository, owner, "Python")

    with pytest.raises(ValueError, match="evidence_count"):
        await repository.update_skill(owner.id, created.id, {"evidence_count": 42})
    with pytest.raises(ValueError, match="last_activity_at"):
        await repository.update_skill(owner.id, created.id, {"last_activity_at": ANCHOR})

    reloaded = await repository.get_skill(owner.id, created.id)
    assert reloaded.evidence_count == 0
    assert reloaded.last_activity_at is None


async def test_a_self_assessment_keeps_its_provenance(repository, db_session, owner):
    """Moving a level writes ``level_source`` alongside it.

    The provenance travels with the level because a level that reached a screen
    without one is the failure this phase exists to prevent. The user is the only
    party who may assert a level here: an estimate has to be derived by something
    that can show its working, and this repository derives nothing.
    """
    created = await _make_skill(repository, owner, "Python")

    updated = await repository.update_skill(
        owner.id,
        created.id,
        {
            "current_level": 2,
            "level_source": SkillLevelSource.USER_DEFINED,
            "confidence": 0,
        },
    )

    assert updated is not None
    current, _target, source, confidence, _evidence, _last = await _skill_columns(
        db_session, created.id
    )
    assert current == 2
    assert source == SkillLevelSource.USER_DEFINED.value
    assert confidence == 0


async def test_an_edit_can_clear_an_optional_field_with_null(repository, owner):
    """A ``None`` is written as SQL ``NULL``, which is how a category is cleared.

    "I no longer call this a framework" has to be expressible, and it must not
    require a second statement to null the column.
    """
    created = await _make_skill(repository, owner, "Django", category="framework")

    updated = await repository.update_skill(owner.id, created.id, {"category": None})

    assert updated is not None
    assert updated.category is None


async def test_recording_evidence_bumps_the_count_and_never_rewinds_recency(
    repository, db_session, owner
):
    """Two activities, the second back-dated before the first.

    The count reaches 2 and ``last_activity_at`` stays at the *newer* instant.
    Moving recency to the instant handed in would let a user back-filling last
    week's log make their most recent activity older than one recorded today,
    and the staleness rule that reads this column would then fire against a skill
    that has been worked on since. A bump is a count of recorded rows and a
    clock; it is not a claim about the person.
    """
    created = await _make_skill(repository, owner, "Python")
    recent = ANCHOR
    older = ANCHOR - timedelta(days=10)

    await repository.record_skill_evidence(owner.id, created.id, recent)
    await repository.record_skill_evidence(owner.id, created.id, older)

    _current, _target, _source, _confidence, evidence, last_seen = await _skill_columns(
        db_session, created.id
    )
    assert evidence == 2
    assert last_seen == recent


async def test_recording_evidence_for_a_foreign_skill_changes_nothing(
    repository, db_session, owner, other
):
    """The bump is owner-scoped, so it returns ``None`` and counts nothing.

    Otherwise a caller could inflate somebody else's evidence count by
    ``POST``ing an activity against an id they guessed — which is exactly the
    "estimate from 6 recorded activities" sentence being manufactured.
    """
    theirs = await _make_skill(repository, other, "Python")

    assert await repository.record_skill_evidence(owner.id, theirs.id, ANCHOR) is None

    _c, _t, _s, _conf, evidence, last_seen = await _skill_columns(db_session, theirs.id)
    assert evidence == 0
    assert last_seen is None


async def test_deleting_a_skill_leaves_its_activities_recorded_against_no_skill(
    repository, owner, db_session
):
    """One skill, one activity — then a delete, and the activity survives.

    ``learning_activities.skill_id`` is ``ON DELETE SET NULL``, not CASCADE. An
    activity is an append-only record that the person spent 45 minutes on
    something, and CASCADE meant that deleting a *skill* destroyed every such
    record silently, from a different table, with nothing said and nothing left
    to notice. The row now outlives its subject with a null pointer.

    The assertion is on what is left, field by field: the account still counts
    **one** activity, and the surviving row still carries its title, its 45
    minutes and its original ``occurred_at``. Only the pointer to the deleted
    skill is gone — so the guarantee is "the record survives", not "the row is
    somehow still there but empty".

    The activity stays out of the skill-scoped reads, which is what a null
    ``skill_id`` means everywhere else in this repository: it belongs to the
    account, not to a skill that no longer exists.
    """
    created = await _make_skill(repository, owner, "Rust")
    activity = await repository.create_activity(
        owner.id,
        title="Read the ownership chapter",
        activity_type=LearningActivityType.STUDY_SESSION,
        skill_id=created.id,
        occurred_at=ANCHOR,
        duration_minutes=45,
    )

    removed = await repository.delete_skill(owner.id, created.id)

    assert removed is True
    assert await repository.get_skill(owner.id, created.id) is None
    assert await repository.delete_skill(owner.id, created.id) is False

    survivors, total = await repository.list_activities(owner.id)
    assert total == 1
    assert len(survivors) == 1
    assert survivors[0].id == activity.id

    title, minutes, occurred_at, skill_id = await _activity_columns(db_session, activity.id)
    assert title == "Read the ownership chapter"
    assert minutes == 45
    assert occurred_at == ANCHOR
    assert skill_id is None

    # Still counted in the account's own totals, and still excluded from the
    # skill-scoped read that has no skill to match against.
    totals = await repository.activity_totals(owner.id)
    assert totals.activities == 1
    assert await repository.list_activities(owner.id, skill_id=created.id) == ([], 0)


async def test_another_accounts_skills_are_invisible(repository, owner, other):
    """A foreign skill is not readable, not findable by name, not deletable.

    All three reads assert the owner themselves rather than trusting a foreign id
    to be harmless — and the name lookup is included because it is the one read
    keyed on a string rather than an id, and therefore the one most likely to be
    written globally by accident.
    """
    theirs = await _make_skill(repository, other, "Python")

    assert await repository.list_skills(owner.id) == ([], 0)
    assert await repository.get_skill(owner.id, theirs.id) is None
    assert await repository.get_skill_by_name(owner.id, "Python") is None
    assert await repository.delete_skill(owner.id, theirs.id) is False
    assert await repository.get_skill(other.id, theirs.id) is not None


# ----------------------------------------------------------------------
# Activities: the window arithmetic the gap rests on
# ----------------------------------------------------------------------


async def test_an_empty_window_is_a_count_of_zero_and_no_measured_minutes(repository, owner):
    """Nothing recorded is ``0`` activities and ``None`` minutes.

    The two are different claims and the pair is the whole point of the phase's
    "a figure that could not be computed is null, never 0" rule. A count of rows
    is always computable, so it is a real zero; a sum over nothing is not a
    measurement of a zero-length window, it is the absence of one, and coercing
    it to ``0`` would say the user studied for no minutes rather than that
    nothing was timed.

    The fixture plants an activity just outside the window, so the assertion is
    about the window rather than about an empty database.
    """
    skill = await _make_skill(repository, owner, "Python")
    await repository.create_activity(
        owner.id,
        title="Read the docs",
        activity_type=LearningActivityType.STUDY_SESSION,
        skill_id=skill.id,
        occurred_at=ANCHOR,
        duration_minutes=30,
    )

    totals = await repository.activity_totals(
        owner.id, since=ANCHOR + timedelta(days=1), until=ANCHOR + timedelta(days=2)
    )

    assert totals.activities == 0
    assert totals.measured_minutes is None
    assert totals.active_days == 0
    assert totals.first_occurred_at is None
    assert totals.last_occurred_at is None


async def test_the_window_counts_activities_distinct_days_and_timed_minutes(repository, owner):
    """Three activities: two on one UTC day, one on the next, one untimed.

    ``activities`` 3 and ``active_days`` 2, because the day is bucketed in UTC
    and the first pair share a date. ``measured_minutes`` is 55 — the two timed
    sessions — not 55/3: an untimed event contributes to the count and to the
    day, and is simply not part of a sum it was never measured for.

    ``active_days`` counts days something was *recorded on*. It is not a count of
    days anybody studied, and the column it reads cannot be turned into one.
    """
    skill = await _make_skill(repository, owner, "Python")
    await repository.create_activity(
        owner.id,
        title="morning session",
        activity_type=LearningActivityType.STUDY_SESSION,
        skill_id=skill.id,
        occurred_at=ANCHOR,
        duration_minutes=45,
    )
    # 23:30 UTC the same day: still the same UTC day, which is what makes the
    # bucket worth asserting at all.
    await repository.create_activity(
        owner.id,
        title="evening page",
        activity_type=LearningActivityType.RESOURCE_VIEWED,
        skill_id=skill.id,
        occurred_at=ANCHOR.replace(hour=23, minute=30),
        duration_minutes=10,
    )
    await repository.create_activity(
        owner.id,
        title="finished the chapter",
        activity_type=LearningActivityType.CONCEPT_LEARNED,
        skill_id=skill.id,
        occurred_at=ANCHOR + timedelta(days=1),
    )

    totals = await repository.activity_totals(owner.id, since=ANCHOR - timedelta(days=1))

    assert totals.activities == 3
    assert totals.measured_minutes == 55
    assert totals.active_days == 2
    assert totals.first_occurred_at == ANCHOR
    assert totals.last_occurred_at == ANCHOR + timedelta(days=1)


async def test_a_window_of_only_events_reports_no_measured_minutes(repository, owner):
    """Three activities, none of them timed: minutes are ``None``, not ``0``.

    ``duration_minutes`` is null for an *event* ("I finished the chapter") and a
    span for a session. A window of only events was searched and found to hold
    nothing that was timed, which is not the same as a window of zero minutes —
    and coalescing the sum to ``0`` is the exact substitution this phase forbids.
    """
    skill = await _make_skill(repository, owner, "Python")
    for offset, title in enumerate(("one", "two", "three")):
        await repository.create_activity(
            owner.id,
            title=title,
            activity_type=LearningActivityType.TASK_COMPLETED,
            skill_id=skill.id,
            occurred_at=ANCHOR + timedelta(days=offset),
        )

    totals = await repository.activity_totals(owner.id, since=ANCHOR - timedelta(days=1))

    assert totals.activities == 3
    assert totals.measured_minutes is None
    assert totals.active_days == 3


async def test_the_per_skill_counts_answer_every_skill_they_were_asked_about(repository, owner):
    """Two skills, one with six recent activities, one with none.

    Every requested id comes back, including the silent one at ``0``. A window
    that was searched and found empty has a real count of zero, and the caller
    needs the ``0`` to say so — the *absence* the phase cares about is a skill
    that was never measured at all, which is a question about the skill row and
    not about this mapping.

    Six is the number from the brief's own example sentence, so the figure a gap
    explanation would quote is the figure the database returns. The activities
    outside the window are the other half of that assertion: an old one is
    counted by the total but not by the window.
    """
    busy = await _make_skill(repository, owner, "Python")
    quiet = await _make_skill(repository, owner, "Rust")
    for day in range(6):
        await repository.create_activity(
            owner.id,
            title=f"session {day}",
            activity_type=LearningActivityType.STUDY_SESSION,
            skill_id=busy.id,
            occurred_at=ANCHOR - timedelta(days=day),
            duration_minutes=30,
        )
    await repository.create_activity(
        owner.id,
        title="an old session",
        activity_type=LearningActivityType.STUDY_SESSION,
        skill_id=busy.id,
        occurred_at=ANCHOR - timedelta(days=90),
    )

    counts = await repository.activity_counts_by_skill(
        owner.id,
        [busy.id, quiet.id],
        since=ANCHOR - timedelta(days=30),
        until=ANCHOR + timedelta(days=1),
    )

    assert counts == {busy.id: 6, quiet.id: 0}


async def test_the_per_skill_counts_ignore_another_accounts_activities(repository, owner, other):
    """The windowed count is owner-scoped like every other read.

    Asserted through the mapping rather than through ``get_activity`` alone,
    because this is the read the gap service depends on: a count that leaked
    another account's rows would inflate an evidence figure the UI presents as
    this person's own.
    """
    theirs = await _make_skill(repository, other, "Python")
    await repository.create_activity(
        other.id,
        title="their session",
        activity_type=LearningActivityType.STUDY_SESSION,
        skill_id=theirs.id,
        occurred_at=ANCHOR,
    )

    counts = await repository.activity_counts_by_skill(
        owner.id, [theirs.id], since=ANCHOR - timedelta(days=1)
    )

    assert counts == {theirs.id: 0}


async def test_the_activity_list_filters_by_skill_goal_type_and_window(repository, owner):
    """Three activities across two skills, two types, and one out-of-window row.

    Every filter is asserted against a fixture where the rows it should exclude
    would otherwise change the answer — the out-of-window activity is also the
    only one of its type, and the other skill's row is also inside the window —
    so a filter that silently did nothing fails here rather than passing.
    """
    python = await _make_skill(repository, owner, "Python")
    rust = await _make_skill(repository, owner, "Rust")
    goal = await repository.create_goal(owner.id, title="Learn Rust")
    await repository.create_activity(
        owner.id,
        title="python session",
        activity_type=LearningActivityType.STUDY_SESSION,
        skill_id=python.id,
        occurred_at=ANCHOR,
        duration_minutes=45,
    )
    await repository.create_activity(
        owner.id,
        title="rust chapter",
        activity_type=LearningActivityType.CONCEPT_LEARNED,
        skill_id=rust.id,
        goal_id=goal.id,
        occurred_at=ANCHOR + timedelta(days=1),
    )
    await repository.create_activity(
        owner.id,
        title="an old session",
        activity_type=LearningActivityType.STUDY_SESSION,
        skill_id=python.id,
        occurred_at=ANCHOR - timedelta(days=60),
    )

    page, total = await repository.list_activities(
        owner.id,
        skill_id=python.id,
        activity_type=LearningActivityType.STUDY_SESSION,
        since=ANCHOR - timedelta(days=30),
        until=ANCHOR + timedelta(days=2),
    )
    by_goal, goal_total = await repository.list_activities(owner.id, goal_id=goal.id)

    assert [row.title for row in page] == ["python session"]
    assert total == 1
    assert [row.title for row in by_goal] == ["rust chapter"]
    assert goal_total == 1


async def test_the_activity_list_is_paged_newest_first_with_a_stable_total(repository, owner):
    """Three activities a day apart, a page of two.

    The page holds the two newest and the total is 3. The second page returns
    the third, which is the assertion that ordering is *total*: ``occurred_at``
    is second-resolution and two activities can share it exactly, which would
    otherwise let a row appear on two pages and another on none.
    """
    skill = await _make_skill(repository, owner, "Python")
    for day, title in ((0, "oldest"), (1, "middle"), (2, "newest")):
        await repository.create_activity(
            owner.id,
            title=title,
            activity_type=LearningActivityType.STUDY_SESSION,
            skill_id=skill.id,
            occurred_at=ANCHOR + timedelta(days=day),
        )

    first_page, total = await repository.list_activities(owner.id, limit=2)
    second_page, second_total = await repository.list_activities(owner.id, limit=2, offset=2)

    assert [row.title for row in first_page] == ["newest", "middle"]
    assert total == 3
    assert [row.title for row in second_page] == ["oldest"]
    assert second_total == 3


async def test_recording_an_activity_keeps_the_source_pointer_labelled(
    repository, db_session, owner
):
    """A repository-derived activity is stored as a repository observation.

    ``source_type``/``source_id`` is the polymorphic pair ``risks.entity_type``/
    ``entity_id`` already uses, and keeping the label is what stops "6 commits
    touched Python files" being read back as "6 Python tasks completed". An
    activity with no pointer at all is a legitimate row — both nulls, no sentinel
    — so the same method writes both shapes.
    """
    skill = await _make_skill(repository, owner, "Python")
    repository_row = uuid.uuid4()

    derived = await repository.create_activity(
        owner.id,
        title="40 commits touched Python files",
        activity_type=LearningActivityType.CODING_ACTIVITY,
        skill_id=skill.id,
        occurred_at=ANCHOR,
        source_type="repository",
        source_id=repository_row,
    )
    manual = await repository.create_activity(
        owner.id,
        title="Read a page",
        activity_type=LearningActivityType.RESOURCE_VIEWED,
        skill_id=skill.id,
        occurred_at=ANCHOR,
    )

    stored = (
        await db_session.execute(
            select(
                LearningActivity.source_type,
                LearningActivity.source_id,
                LearningActivity.duration_minutes,
            ).where(LearningActivity.id == derived.id)
        )
    ).one()
    assert stored == ("repository", repository_row, None)
    # An untimed event stays null rather than zero: "I opened the page" is not
    # "I spent zero minutes on it".
    assert (await repository.activity_totals(owner.id)).measured_minutes is None
    assert manual.source_type is None
    assert manual.source_id is None


async def test_recording_an_activity_takes_no_time_of_its_own(repository, db_session, owner):
    """``occurred_at`` falls back to the database clock, and nothing rewrites it.

    An activity is append-only: there is no update method on the table, because
    an ``updated_at`` stamp on a fact about a moment would assert the moment is
    still being revised. When the caller supplies no instant, the database's is
    the only defensible default.
    """
    before = await _now(db_session)
    created = await repository.create_activity(
        owner.id,
        title="Opened a page",
        activity_type=LearningActivityType.RESOURCE_VIEWED,
    )
    after = await _now(db_session)

    stored = await db_session.scalar(
        select(LearningActivity.occurred_at).where(LearningActivity.id == created.id)
    )
    assert before <= stored <= after
    assert not hasattr(LearningActivity, "updated_at")


async def test_an_unknown_activity_type_or_a_negative_duration_is_refused(repository, owner):
    """Both are mistakes about the calling code, and neither reaches storage.

    ``activity_type`` is what separates a weighted study session from an
    unweighted page view, so an unrecognised value would silently land in
    whichever bucket the query forgot to exclude — and the evidence count behind
    a skill level would then overstate itself.
    """
    with pytest.raises(ValueError, match="activity type"):
        await repository.create_activity(owner.id, title="Something", activity_type="deep work")
    with pytest.raises(ValueError, match="duration"):
        await repository.create_activity(
            owner.id,
            title="Something",
            activity_type=LearningActivityType.STUDY_SESSION,
            duration_minutes=-5,
        )

    assert (await repository.activity_totals(owner.id)).activities == 0


async def test_another_accounts_activities_are_not_reachable(repository, owner, other):
    """A foreign account's history is invisible to both the list and the totals.

    The totals are asserted as well as the list, because the totals are what the
    dashboard tiles read: a list that leaked rows the aggregates did not would
    render a chart nobody could reproduce.
    """
    theirs = await _make_skill(repository, other, "Python")
    await repository.create_activity(
        other.id,
        title="their session",
        activity_type=LearningActivityType.STUDY_SESSION,
        skill_id=theirs.id,
        occurred_at=ANCHOR,
        duration_minutes=120,
    )
    theirs_id = theirs.id

    assert await repository.list_activities(owner.id) == ([], 0)
    totals = await repository.activity_totals(owner.id, since=ANCHOR - timedelta(days=1))
    assert (totals.activities, totals.measured_minutes) == (0, None)
    assert await repository.activity_counts_by_skill(owner.id, [theirs_id]) == {theirs_id: 0}


# ----------------------------------------------------------------------
# The honest answer when there is nothing to answer with
# ----------------------------------------------------------------------


async def test_the_repository_answers_an_account_that_has_recorded_nothing(repository, owner):
    """Every read on an empty account is empty rather than zero-shaped.

    A fresh account is the case the phase's rules are hardest on, because a
    dashboard that renders "0 activities, 0 minutes, 0 skills" as zeroes would be
    claiming measurements that were never taken. Lists are empty, counts are
    genuinely zero, the one derived figure is ``None``, and the per-skill mapping
    covers exactly what it was asked about.
    """
    assert await repository.list_goals(owner.id) == ([], 0)
    assert await repository.list_skills(owner.id) == ([], 0)
    assert await repository.list_activities(owner.id) == ([], 0)
    assert await repository.count_goals(owner.id) == 0
    assert await repository.count_skills(owner.id) == 0

    totals = await repository.activity_totals(owner.id)
    assert totals.activities == 0
    assert totals.measured_minutes is None
    assert totals.active_days == 0
    assert await repository.activity_counts_by_skill(owner.id, []) == {}
