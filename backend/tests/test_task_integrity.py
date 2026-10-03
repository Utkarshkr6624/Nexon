"""Task integrity: a subtask may never be separated from the board it sits on.

:meth:`app.services.task_service.TaskService.update` is where a ``PATCH /tasks/{id}``
becomes a row, and it is the only place that can write ``project_id`` and
``parent_id`` on an existing task. Everything in this file is a claim about what
that method must refuse, because both halves of the subtask tree are two
*independent* foreign keys and the schema cannot hold them still:

    tasks.project_id  ->  projects.id   ON DELETE CASCADE
    tasks.parent_id   ->  tasks.id      ON DELETE CASCADE

Nothing about a row with ``project_id = P2`` and ``parent_id`` pointing into P1
is invalid. Both columns hold real ids, so the row commits, it serves, and it
renders on P2's board. It is discovered only later, as **data loss**: the first
``DELETE`` of P1 removes the P1 parent, and that row's own cascade takes the P2
subtask with it. An ordinary edit destroyed a task the user was looking at, with
no tombstone and no way back.

What this file pins
-------------------
Four refusals and three permissions, all through the service rather than over
HTTP, because the rules live in the service and a route test would be testing the
router:

* **Refused** — a subtask moved to a project its parent is not in.
* **Refused** — a task that already has subtasks given a parent of its own,
  which :func:`~app.services.task_service._check_parent` cannot see: it inspects
  the *proposed* parent and never what the task being moved already parents.
  Without the check, ``C -> A -> B`` is a depth of two, which is exactly what
  ``_MAX_SUBTASK_DEPTH = 1`` forbids.
* **Refused** — a parent with children moved out from under them (pre-existing
  behaviour, kept green here because the same ``if`` was restructured).
* **Refused** — another account's project: ``NotFoundError``, never a 403.
* **Allowed** — a root card with no children moving between two of the caller's
  own projects.
* **Allowed** — a subtask detaching (``parent_id: null``) and then moving, which
  is the way out the refusal message names.
* **Allowed** — a subtask moving to a different root card in the same project.

Both refusals are 422s with a sentence a user can act on, and both are
**conditional on the PATCH changing the linkage**. A client that echoes back the
``parent_id`` it read must not have an unrelated title edit rejected for it.

House style, deliberately
-------------------------
Follows ``tests/test_developer_service.py``:

* ``pytestmark = pytest.mark.integration`` — every test here needs the live
  PostgreSQL the suite truncates between tests.
* Services are hand-wired in a module-level ``_service()`` helper mirroring
  ``app.api.deps``, with the **real** activity sink: a ``None`` sink would make
  the "nothing was recorded for a refusal" assertion pass vacuously.
* Rows are read back through **explicit column tuples**, never ORM entities. The
  session that wrote them is the session reading them, so an entity read would
  return whatever the identity map cached and an "unchanged" assertion would
  compare a stale object with itself.
* The clock is read from the database through ``_db_now``, never from
  ``datetime.now()``: ``updated_at`` is ``server_default=now()`` with
  ``onupdate=now()``, so it is the one column that can say whether a row was
  written at all, and it has to be compared against the clock that set it.

Fixture rows go through ``TaskService.create`` rather than through the ORM,
because ``create`` is where the subtask rules are *also* enforced and a fixture
built underneath them would be testing a tree the service would refuse to build.

Every expected figure below is derived in the test's own docstring rather than
recorded from a run.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models.activity import ActivityLog
from app.models.enums import ActivityEvent
from app.models.project import Project
from app.models.task import Task
from app.models.user import User
from app.repositories.activity import ActivityRepository
from app.repositories.project import ProjectRepository
from app.repositories.tag import TagRepository
from app.repositories.task import TaskRepository
from app.schemas.task import TaskCreate, TaskUpdate
from app.services.activity_service import ActivityService
from app.services.task_service import TaskService
from tests.analytics_fixtures import AnalyticsSeed, register_user

pytestmark = pytest.mark.integration

#: The refusal the API shows for a subtask that was moved away from its parent.
#:
#: Asserted as a literal rather than imported from the service: this string is
#: written for the person who pressed save, so it is part of the contract rather
#: than an implementation detail, and a test that read it back out of the module
#: would agree with whatever the module happened to say.
SUBTASK_PROJECT_MOVE = (
    "A subtask cannot be moved to another project on its own, because its parent "
    "would stay behind on the old one. Detach it from its parent first, then move it."
)

#: The refusal for naming a parent for a task that already has children.
PARENT_WITH_SUBTASKS_CANNOT_NEST = (
    "This task already has subtasks of its own, so it cannot become a subtask "
    "itself; nesting is limited to one level."
)

#: The pre-existing refusal for moving a parent out from under its children.
PARENT_MOVE_BLOCKED = "Move or delete this task's subtasks before moving the task itself."

#: The project lookup's own answer, which must never become a 403.
PROJECT_NOT_FOUND = "Project not found."

#: The two activity event types these fixtures can produce. Named in full rather
#: than filtered on a prefix so an event the task service starts writing is a
#: test failure rather than a silently ignored row.
_TASK_EVENTS: tuple[str, ...] = (
    ActivityEvent.TASK_CREATED.value,
    ActivityEvent.TASK_UPDATED.value,
)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _service(session: AsyncSession) -> TaskService:
    """A task service wired the way ``app.api.deps`` wires it.

    Every collaborator is the real one — the three repositories the service was
    constructed with in ``get_task_service``, and the activity sink. The sink is
    what the "a refusal records nothing" assertions below count against; a
    ``None`` would make them pass for the wrong reason.

    Args:
        session: The test session. Every repository is built on this same one,
            which is why the reads below go through explicit columns.
    """
    return TaskService(
        TaskRepository(session),
        ProjectRepository(session),
        TagRepository(session),
        activity=ActivityService(ActivityRepository(session)),
    )


async def _owner(session: AsyncSession, username: str = "ada") -> User:
    """One account, inserted directly.

    Direct because these tests drive the service rather than a route, and
    ``register_user`` writes no activity events — which matters, because the
    event assertions below count event types.
    """
    return await register_user(session, username=username)


async def _db_now(session: AsyncSession) -> datetime:
    """The database's clock, as an aware UTC instant.

    Used to prove that a row the service refused to touch still carries the
    ``updated_at`` its creation gave it: ``updated_at`` is ``onupdate=now()``, so
    it moves on *any* write, and comparing it against the database's own clock is
    the cheapest way to see that nothing happened.
    """
    value = await session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Reading stored rows back
# ---------------------------------------------------------------------------

#: Every column of ``tasks``, listed in full so a column added to the model
#: shows up here rather than as a silently unasserted field in the before/after
#: comparisons. The "the row is untouched" claims are made against this whole
#: tuple, not against a convenient subset of it.
_TASK_COLUMNS = (
    Task.id,
    Task.project_id,
    Task.owner_id,
    Task.parent_id,
    Task.title,
    Task.description,
    Task.status,
    Task.priority,
    Task.start_date,
    Task.due_date,
    Task.estimated_minutes,
    Task.actual_minutes,
    Task.completed_at,
    Task.position,
    Task.created_at,
    Task.updated_at,
)


async def _exists(session: AsyncSession, task_id: uuid.UUID) -> bool:
    """Whether a task row is still stored.

    A ``COUNT`` rather than ``session.get(Task, ...)``: this session is the one
    that wrote the rows, so an ORM identity-map lookup can hand back a row the
    database no longer has.
    """
    found = await session.scalar(select(func.count()).select_from(Task).where(Task.id == task_id))
    return bool(found)


async def _task(session: AsyncSession, task_id: uuid.UUID) -> dict[str, object]:
    """One stored task, as a plain mapping over :data:`_TASK_COLUMNS`."""
    result = await session.execute(select(*_TASK_COLUMNS).where(Task.id == task_id))
    row = result.one_or_none()
    assert row is not None, f"no stored task with id {task_id}"
    return dict(row._mapping)


async def _tasks(session: AsyncSession, owner_id: uuid.UUID) -> list[dict[str, object]]:
    """Every task one account owns, ordered by title.

    By title rather than by id: the ids are UUIDs generated per row, so an
    id-ordered listing is in a different order on every run and any positional
    assertion built on it would be asserting nothing. Titles are written by the
    fixture and are stable.
    """
    result = await session.execute(
        select(*_TASK_COLUMNS).where(Task.owner_id == owner_id).order_by(Task.title.asc())
    )
    return [dict(row._mapping) for row in result.all()]


def _titles_in(rows: list[Mapping[str, object]], project_id: uuid.UUID) -> list[str]:
    """The titles of ``rows`` that sit in ``project_id``, alphabetically."""
    return sorted(str(row["title"]) for row in rows if row["project_id"] == project_id)


def _subtask_titles(tasks: Sequence[Task]) -> list[str]:
    """The titles the service reports as the direct children of a card.

    Through :meth:`~app.services.task_service.TaskService.list_subtasks` rather
    than through a query of this file's own, so the assertion covers what a board
    would actually render rather than what a second code path agrees with.
    """
    return sorted(task.title for task in tasks)


def _depth(row: Mapping[str, object], by_id: Mapping[uuid.UUID, Mapping[str, object]]) -> int:
    """How many parents a task has above it, with a hard stop.

    The cap exists so a *corrupt* tree fails the assertion instead of hanging the
    suite: this helper is called on every row, including rows the invariants
    above are supposed to make impossible. Five is two levels more than
    ``_MAX_SUBTASK_DEPTH`` allows, so a valid tree never reaches it.
    """
    depth = 0
    node = row
    while node["parent_id"] is not None and depth < 5:
        depth += 1
        node = by_id[node["parent_id"]]  # type: ignore[index]
    return depth


async def _max_depth(session: AsyncSession, owner_id: uuid.UUID) -> int:
    """The deepest chain of parents anywhere in one account's task tree."""
    rows = await _tasks(session, owner_id)
    by_id = {row["id"]: row for row in rows}  # type: ignore[misc]
    return max((_depth(row, by_id) for row in rows), default=0)


async def _event_counts(session: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    """``{event type: how many}`` for this account's task history.

    Counts by type rather than an ordered list: the claim under test is *which
    facts were recorded*, and the order the database chose to write three
    transactions in is not a fact about the product.

    Both keys are always present, defaulted to zero. A ``GROUP BY`` omits an
    event type that never fired, so an account with no ``TASK_UPDATED`` would
    otherwise return a mapping missing the key — and a test asserting "nothing
    was recorded" against a mapping that silently lacks the entry would pass for
    the wrong reason on one side and fail on the other.
    """
    result = await session.execute(
        select(ActivityLog.event_type, func.count())
        .where(
            ActivityLog.user_id == user_id,
            ActivityLog.event_type.in_(_TASK_EVENTS),
        )
        .group_by(ActivityLog.event_type)
    )
    counts: dict[str, int] = dict.fromkeys(_TASK_EVENTS, 0)
    for row in result.all():
        counts[str(row[0])] = int(row[1])
    return counts


# ---------------------------------------------------------------------------
# Fixture rows, built through the service
# ---------------------------------------------------------------------------


async def _card(
    session: AsyncSession, service: TaskService, owner: User, project_id: uuid.UUID, title: str
) -> Task:
    """A root card — ``parent_id`` null — in one of the caller's projects."""
    return await service.create(owner=owner, data=TaskCreate(project_id=project_id, title=title))


async def _subtask(
    session: AsyncSession,
    service: TaskService,
    owner: User,
    project_id: uuid.UUID,
    parent: Task,
    title: str,
) -> Task:
    """A subtask under ``parent``, created the way the product creates one."""
    return await service.create(
        owner=owner,
        data=TaskCreate(project_id=project_id, title=title, parent_id=parent.id),
    )


# ---------------------------------------------------------------------------
# (a) Cross-project reassignment of a subtask — the blocker
# ---------------------------------------------------------------------------


async def test_a_subtask_cannot_be_moved_into_another_project_and_the_row_is_untouched(
    db_session: AsyncSession,
) -> None:
    """Three rows, one refused PATCH, and every column of the subtask unchanged.

    The fixture is: project **P1 = Atlas**, project **P2 = Beacon**, and in Atlas
    a root card ``Draft the plan`` with one subtask ``Write the outline``. Beacon
    is empty. So the counts to expect afterwards are **2** tasks in Atlas (the card
    and its subtask) and **0** in Beacon, with **1** child still under the card and
    a maximum parent-chain depth of **1**.

    The refused PATCH is ``{"project_id": <Beacon>}`` sent to the *subtask*, which
    is the ordinary edit the blocker describes. Before the fix it wrote
    ``subtask.project_id = Beacon`` while ``parent_id`` still named a card in
    Atlas. Nothing downstream objected: both are real ids, the row renders on
    Beacon's board, and the first ``DELETE /projects/Atlas`` takes the Atlas
    parent with it — and that row's own ``ON DELETE CASCADE`` then takes the
    Beacon subtask. Permanent cross-project loss from an ordinary edit.

    **Every column is compared, not just ``project_id``.** The whole
    :data:`_TASK_COLUMNS` tuple is read before the call and again after, through
    an explicit projection, so "refused" has to mean *the row is byte-identical*
    rather than *the interesting column happens to be unchanged*.
    ``updated_at`` is the sharpest of those columns — it carries
    ``onupdate=func.now()``, so any write at all, even one that wrote the value
    back unchanged, would move it.

    **The refusal is a 422, not a 409 and not a 500.** It is a property of the
    requested shape rather than a collision with a concurrent writer, and the
    message has to name the reason and the way out, because the caller is the
    only one who can fix it.

    **Nothing is recorded.** The account's ``TASK_UPDATED`` count stays at **0**
    and ``TASK_CREATED`` at **2** (the card and the subtask). A feed that
    announced an edit that never happened would be a second, quieter lie on top
    of the first one.
    """
    ada = await _owner(db_session)
    seed = AnalyticsSeed(db_session, ada)
    atlas = await seed.project(name="Atlas")
    beacon = await seed.project(name="Beacon")
    service = _service(db_session)
    card = await _card(db_session, service, ada, atlas.id, "Draft the plan")
    child = await _subtask(db_session, service, ada, atlas.id, card, "Write the outline")
    before = await _task(db_session, child.id)
    card_before = await _task(db_session, card.id)

    with pytest.raises(ValidationError) as refused:
        await service.update(task=child, data=TaskUpdate(project_id=beacon.id), owner=ada)

    assert str(refused.value) == SUBTASK_PROJECT_MOVE
    assert refused.value.status_code == 422
    assert await _task(db_session, child.id) == before
    assert await _task(db_session, card.id) == card_before

    rows = await _tasks(db_session, ada.id)
    assert _titles_in(rows, atlas.id) == ["Draft the plan", "Write the outline"]
    assert _titles_in(rows, beacon.id) == []
    assert await _max_depth(db_session, ada.id) == 1
    assert await _event_counts(db_session, ada.id) == {
        ActivityEvent.TASK_CREATED.value: 2,
        ActivityEvent.TASK_UPDATED.value: 0,
    }
    # The stored instant is the database's own, and it is not in the future —
    # which is the only reading of "nothing was written" the model allows.
    assert isinstance(before["updated_at"], datetime)
    assert before["updated_at"] <= await _db_now(db_session)


async def test_deleting_the_old_project_now_takes_the_subtask_with_its_own_board(
    db_session: AsyncSession,
) -> None:
    """The cascade that used to destroy a task on another board now destroys nothing else.

    This is the consequence of the refusal above, asserted end to end. The
    fixture is one project **Atlas** holding a card and its subtask, and one empty
    project **Beacon**. Deleting Atlas through
    :meth:`~app.repositories.project.ProjectRepository.delete` — which is what
    ``DELETE /projects/{id}`` reaches — must leave **0** tasks behind, and the
    subtask must go with the card it belongs to rather than with something on
    another board.

    In the pre-fix world this same sequence destroyed a task on Beacon's board:
    the subtask had been *moved* to Beacon while its parent stayed in Atlas, and
    Atlas's delete removed the parent card, whose own cascade then took the
    subtask with it. Pinning the count at zero after the delete is the claim that
    the cross-board task no longer exists to be destroyed, because the refusal in
    the previous test stopped it being created.
    """
    ada = await _owner(db_session)
    seed = AnalyticsSeed(db_session, ada)
    atlas = await seed.project(name="Atlas")
    beacon = await seed.project(name="Beacon")
    service = _service(db_session)
    card = await _card(db_session, service, ada, atlas.id, "Draft the plan")
    child = await _subtask(db_session, service, ada, atlas.id, card, "Write the outline")

    await ProjectRepository(db_session).delete(atlas)

    assert await _tasks(db_session, ada.id) == []
    assert await _exists(db_session, card.id) is False
    assert await _exists(db_session, child.id) is False
    assert await db_session.get(Project, beacon.id) is not None
    assert await _tasks(db_session, beacon.id) == []


# ---------------------------------------------------------------------------
# (b) The permissions: what must still work
# ---------------------------------------------------------------------------


async def test_a_root_card_with_no_subtasks_can_still_be_moved_between_the_callers_own_projects(
    db_session: AsyncSession,
) -> None:
    """The ordinary move the refusal must not have broken: Atlas -> Beacon, one row.

    The fixture is a single root card ``Draft the plan`` in **Atlas**, with
    ``parent_id`` null and no children — which is the only shape the invariant
    leaves free, since a task with a parent cannot change projects and a task
    with children cannot move either. After the move, **1** task sits in Beacon
    and **0** in Atlas, ``parent_id`` is still null, and the title is unchanged,
    because a move is not an edit of anything but the project.

    This is the counterweight to the refusal: a rule that refuses everything is
    not a rule, it is an outage, and "moving a card to another board" is the
    single most common edit the product offers. ``TASK_UPDATED`` goes to **1**.
    """
    ada = await _owner(db_session)
    seed = AnalyticsSeed(db_session, ada)
    atlas = await seed.project(name="Atlas")
    beacon = await seed.project(name="Beacon")
    service = _service(db_session)
    card = await _card(db_session, service, ada, atlas.id, "Draft the plan")

    updated = await service.update(task=card, data=TaskUpdate(project_id=beacon.id), owner=ada)

    assert updated.project_id == beacon.id
    stored = await _task(db_session, card.id)
    assert stored["project_id"] == beacon.id
    assert stored["parent_id"] is None
    assert stored["title"] == "Draft the plan"
    rows = await _tasks(db_session, ada.id)
    assert _titles_in(rows, beacon.id) == ["Draft the plan"]
    assert _titles_in(rows, atlas.id) == []
    assert await _event_counts(db_session, ada.id) == {
        ActivityEvent.TASK_CREATED.value: 1,
        ActivityEvent.TASK_UPDATED.value: 1,
    }


async def test_a_subtask_can_still_be_detached_from_its_parent_and_then_moved(
    db_session: AsyncSession,
) -> None:
    """The way out the refusal message names: detach, then move. Two writes, both legal.

    The fixture is Atlas holding a card and its subtask, and an empty Beacon. The
    first PATCH is ``{"parent_id": null}`` on the subtask, which makes it a root
    card *in Atlas* — Atlas still holds **2** tasks and the card still has **1**
    child, namely nobody, so the maximum depth drops from **1** to **0** because
    the chain is now one node long. The second PATCH is ``{"project_id": Beacon}``
    on the same subtask, which is then the shape the previous test proves legal:
    Beacon holds **1**, Atlas holds **1**, and the card is left where it was.

    Asserted separately because the two steps fail for different reasons and a
    single test would not say which one broke. It is also the test that stops the
    refusal from becoming a dead end — a rule with no way through it is a rule
    users route around, and the route around this one is a direct SQL write.
    """
    ada = await _owner(db_session)
    seed = AnalyticsSeed(db_session, ada)
    atlas = await seed.project(name="Atlas")
    beacon = await seed.project(name="Beacon")
    service = _service(db_session)
    card = await _card(db_session, service, ada, atlas.id, "Draft the plan")
    child = await _subtask(db_session, service, ada, atlas.id, card, "Write the outline")

    detached = await service.update(task=child, data=TaskUpdate(parent_id=None), owner=ada)

    assert detached.parent_id is None
    after_detach = await _task(db_session, child.id)
    assert after_detach["project_id"] == atlas.id
    assert after_detach["parent_id"] is None
    rows = await _tasks(db_session, ada.id)
    assert _titles_in(rows, atlas.id) == ["Draft the plan", "Write the outline"]
    assert await _max_depth(db_session, ada.id) == 0
    assert len(await service.list_subtasks(task=card, owner=ada)) == 0

    moved = await service.update(task=child, data=TaskUpdate(project_id=beacon.id), owner=ada)

    assert moved.project_id == beacon.id
    rows = await _tasks(db_session, ada.id)
    assert _titles_in(rows, beacon.id) == ["Write the outline"]
    assert _titles_in(rows, atlas.id) == ["Draft the plan"]
    assert (await _task(db_session, card.id))["parent_id"] is None
    assert await _event_counts(db_session, ada.id) == {
        ActivityEvent.TASK_CREATED.value: 2,
        ActivityEvent.TASK_UPDATED.value: 2,
    }


# ---------------------------------------------------------------------------
# (c) Depth: a parent that already has children
# ---------------------------------------------------------------------------


async def test_giving_a_parent_to_a_task_that_already_has_subtasks_is_refused(
    db_session: AsyncSession,
) -> None:
    """``C -> A -> B`` is refused, and the tree is exactly as it was.

    The fixture is two root cards in **Atlas** — ``Draft the plan`` (call it **A**)
    and ``Prepare the review`` (call it **C**) — with **1** subtask ``Write the
    outline`` (**B**) under A. The refused PATCH is ``{"parent_id": C}`` on **A**.
    The expected figures afterwards are therefore: **2** root cards in Atlas,
    **1** subtask, B's parent still A, A's parent still null, and a maximum
    parent-chain depth of **1** — not the **2** the request would have produced.

    **Why the old check missed it.** :func:`~app.services.task_service._check_parent`
    refuses a *proposed parent* that is itself a subtask, which is what stops a
    grandchild being created by *choosing* a parent. It cannot see the other end
    of the edge: that **A** is itself a parent. A's own depth was never consulted,
    so ``A -> C`` was accepted and C gained a child that already had children.
    The result renders as three cards with a level the board does not draw and
    that every progress roll-up would have to special-case.

    The refusal carries structured details — ``max_depth`` **1** and the number of
    subtasks that would have been orphaned from the root level, **1** — so a
    client can say "this card has 1 subtask" without a second request. Naming the
    children themselves would be a leak; naming their count is not.

    And the refusal changes nothing: the full column tuple of A and of B is
    identical before and after, and ``TASK_UPDATED`` stays at **0** while
    ``TASK_CREATED`` is **3**.
    """
    ada = await _owner(db_session)
    seed = AnalyticsSeed(db_session, ada)
    atlas = await seed.project(name="Atlas")
    service = _service(db_session)
    a_card = await _card(db_session, service, ada, atlas.id, "Draft the plan")
    c_card = await _card(db_session, service, ada, atlas.id, "Prepare the review")
    b_child = await _subtask(db_session, service, ada, atlas.id, a_card, "Write the outline")
    a_before = await _task(db_session, a_card.id)
    b_before = await _task(db_session, b_child.id)

    with pytest.raises(ValidationError) as refused:
        await service.update(task=a_card, data=TaskUpdate(parent_id=c_card.id), owner=ada)

    assert str(refused.value) == PARENT_WITH_SUBTASKS_CANNOT_NEST
    assert refused.value.status_code == 422
    assert refused.value.details == {"max_depth": 1, "subtasks": 1}

    assert await _task(db_session, a_card.id) == a_before
    assert await _task(db_session, b_child.id) == b_before
    assert a_before["parent_id"] is None
    assert b_before["parent_id"] == a_card.id

    rows = await _tasks(db_session, ada.id)
    assert [row["parent_id"] for row in rows if row["title"] != "Write the outline"] == [
        None,
        None,
    ]
    assert await _max_depth(db_session, ada.id) == 1
    assert _subtask_titles(await service.list_subtasks(task=a_card, owner=ada)) == [
        "Write the outline"
    ]
    assert _subtask_titles(await service.list_subtasks(task=c_card, owner=ada)) == []
    assert await _event_counts(db_session, ada.id) == {
        ActivityEvent.TASK_CREATED.value: 3,
        ActivityEvent.TASK_UPDATED.value: 0,
    }


async def test_a_subtask_can_still_be_moved_to_another_root_card_in_the_same_project(
    db_session: AsyncSession,
) -> None:
    """Re-parenting at depth one still works, and is recorded as **1** update.

    The fixture is **Atlas** with three cards: two root cards ``Draft the plan``
    (A) and ``Prepare the review`` (C), and one subtask ``Write the outline`` (B)
    under A. The PATCH moves B from A to C. The expected figures are the same
    three rows and the same maximum depth of **1** before and after — a re-parent
    within one level changes which card a subtask hangs under, not how deep the
    tree is — and exactly **1** ``TASK_UPDATED``.

    This is the permission half of the depth rule. The refusal above is only
    meaningful if the ordinary operation it sits next to still works, and
    "re-parent a subtask to a different card on the same board" is an ordinary
    operation. Its title is also unchanged, which is what distinguishes a move
    from an edit.
    """
    ada = await _owner(db_session)
    seed = AnalyticsSeed(db_session, ada)
    atlas = await seed.project(name="Atlas")
    service = _service(db_session)
    a_card = await _card(db_session, service, ada, atlas.id, "Draft the plan")
    c_card = await _card(db_session, service, ada, atlas.id, "Prepare the review")
    b_child = await _subtask(db_session, service, ada, atlas.id, a_card, "Write the outline")

    moved = await service.update(task=b_child, data=TaskUpdate(parent_id=c_card.id), owner=ada)

    assert moved.parent_id == c_card.id
    stored = await _task(db_session, b_child.id)
    assert stored["parent_id"] == c_card.id
    assert stored["project_id"] == atlas.id
    assert stored["title"] == "Write the outline"
    assert _subtask_titles(await service.list_subtasks(task=a_card, owner=ada)) == []
    assert _subtask_titles(await service.list_subtasks(task=c_card, owner=ada)) == [
        "Write the outline"
    ]
    assert await _max_depth(db_session, ada.id) == 1
    assert await _event_counts(db_session, ada.id) == {
        ActivityEvent.TASK_CREATED.value: 3,
        ActivityEvent.TASK_UPDATED.value: 1,
    }


# ---------------------------------------------------------------------------
# (d) Ownership, and the guards that were already there
# ---------------------------------------------------------------------------


async def test_another_accounts_project_is_not_found_and_never_a_forbidden(
    db_session: AsyncSession,
) -> None:
    """Grace's project id on Ada's subtask is a 404 — and it beats the linkage refusal.

    The fixture gives Ada an **Atlas** with a card and its subtask, and Grace a
    **Beacon** of her own. Ada's PATCH is ``{"project_id": Grace's Beacon}`` on her
    own subtask, which is *also* a move that would separate a subtask from its
    parent — so this request trips two rules at once and the ordering is the
    claim.

    **Not found, never forbidden.** ``project_id`` is user input, so the
    destination is resolved through the owner-scoped project lookup before
    anything else looks at it. A 403 would confirm that Grace's Beacon is a real
    project, which is exactly the enumeration oracle the 404 rule exists to close:
    the answer has to be identical to the one for a project id nobody ever issued.
    Both are asserted so the two ids cannot be told apart even by status code.

    **The scoped lookup runs first**, so the 404 is what Ada gets rather than the
    linkage refusal — a refusal whose message talks about *her* subtask's parent
    is a slightly better oracle than a bare 404 is a worse one, and neither is
    needed: the ownership question is the one that can be answered definitively
    from the database. Ada's row is untouched, and ``TASK_UPDATED`` stays at **0**.
    """
    ada = await _owner(db_session, "ada")
    grace = await _owner(db_session, "grace")
    atlas = await AnalyticsSeed(db_session, ada).project(name="Atlas")
    grace_beacon = await AnalyticsSeed(db_session, grace).project(name="Beacon")
    service = _service(db_session)
    card = await _card(db_session, service, ada, atlas.id, "Draft the plan")
    child = await _subtask(db_session, service, ada, atlas.id, card, "Write the outline")
    before = await _task(db_session, child.id)

    with pytest.raises(NotFoundError) as foreign:
        await service.update(task=child, data=TaskUpdate(project_id=grace_beacon.id), owner=ada)
    with pytest.raises(NotFoundError) as invented:
        await service.update(task=child, data=TaskUpdate(project_id=uuid.uuid4()), owner=ada)

    assert str(foreign.value) == str(invented.value) == PROJECT_NOT_FOUND
    assert foreign.value.status_code == invented.value.status_code == 404
    assert await _task(db_session, child.id) == before
    assert (await _task(db_session, card.id))["parent_id"] is None
    assert await _tasks(db_session, grace.id) == []
    assert await _event_counts(db_session, ada.id) == {
        ActivityEvent.TASK_CREATED.value: 2,
        ActivityEvent.TASK_UPDATED.value: 0,
    }


async def test_a_parent_with_subtasks_still_cannot_be_moved_to_another_project(
    db_session: AsyncSession,
) -> None:
    """The pre-existing half of the move guard, kept green after it was restructured.

    The fixture is **Atlas** with a card and its subtask, plus an empty
    **Beacon**. The PATCH moves the **card** — the parent, not the child — and the
    expected answer is the long-standing 422 whose message tells the user what to
    do: **2** tasks stay in Atlas, **0** in Beacon, and the card still has its
    **1** child.

    This is here because the same ``if`` in :meth:`TaskService.update` was
    restructured to also carry the subtask guard, and a guard whose neighbours
    moved is a guard that quietly stopped firing. It is the mirror image of the
    blocker: here the *parent* is being moved, there the *child* is, and the same
    broken linkage — a ``parent_id`` naming a task in another project — is what
    each has to prevent.
    """
    ada = await _owner(db_session)
    seed = AnalyticsSeed(db_session, ada)
    atlas = await seed.project(name="Atlas")
    beacon = await seed.project(name="Beacon")
    service = _service(db_session)
    card = await _card(db_session, service, ada, atlas.id, "Draft the plan")
    child = await _subtask(db_session, service, ada, atlas.id, card, "Write the outline")
    before = await _task(db_session, card.id)

    with pytest.raises(ValidationError) as refused:
        await service.update(task=card, data=TaskUpdate(project_id=beacon.id), owner=ada)

    assert str(refused.value) == PARENT_MOVE_BLOCKED
    assert refused.value.status_code == 422
    assert await _task(db_session, card.id) == before
    rows = await _tasks(db_session, ada.id)
    assert _titles_in(rows, atlas.id) == ["Draft the plan", "Write the outline"]
    assert _titles_in(rows, beacon.id) == []
    assert _subtask_titles(await service.list_subtasks(task=card, owner=ada)) == [
        "Write the outline"
    ]
    assert (await _task(db_session, child.id))["project_id"] == atlas.id
    assert await _event_counts(db_session, ada.id) == {
        ActivityEvent.TASK_CREATED.value: 2,
        ActivityEvent.TASK_UPDATED.value: 0,
    }
