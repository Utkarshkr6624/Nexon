"""``PUT /availability`` must replace a week or change nothing at all.

``replace_for_user`` used to call ``delete_for_user`` — which commits — and only
then insert the submitted rules. So the moment the table refused the payload,
the user's whole week was already gone and durable: two rules in, one duplicate
on the way back, zero rules stored, and the caller told "conflict" about a change
that had silently deleted their calendar. The audit measured exactly that, 2 to 0.

Everything here is written against that failure, and every expectation is
derived from the fixture in the test's own docstring rather than recorded from a
run. Three properties are pinned:

**A successful replace stores exactly what was sent** — the whole submitted week
and nothing of the previous one.

**A refused replace leaves the original week completely intact**, checked from a
*second connection*. Reading through the same session would prove nothing: an
uncommitted delete is invisible to itself either way, so only another connection
can tell whether the delete was rolled back or committed. That is the difference
between this file and a test that passes against the bug it was written for.

**No partial state is ever observable** — a payload whose *second* rule is the
refused one leaves neither the first rule of that payload nor any trace of the
attempt.

Rows are read back through explicit column projections, and rows are read from
a fresh session rather than through the ORM, because the ORM would hand back the
identity map's idea of a row it is itself in the middle of writing.
"""

from __future__ import annotations

import uuid
from datetime import time
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.models.planner import AvailabilityRule
from app.repositories.planner import AvailabilityRuleRepository
from tests.analytics_fixtures import register_user

pytestmark = pytest.mark.integration

#: The columns every assertion reads: what the user submitted, and nothing the
#: database invented. ``id`` and the timestamps are deliberately absent — they are
#: generated per row and say nothing about whether the week survived.
_RULE_COLUMNS = (
    AvailabilityRule.weekday,
    AvailabilityRule.starts_at,
    AvailabilityRule.ends_at,
    AvailabilityRule.label,
)

#: The week an account starts every test with: Monday 09:00-12:00 "core" and
#: Tuesday 10:00-16:00. Two rules, on two different weekdays, so a replace that
#: keeps only part of the payload is distinguishable from one that keeps it all.
ORIGINAL_WEEK = [
    (0, time(9, 0), time(12, 0), "core"),
    (1, time(10, 0), time(16, 0), None),
]


@pytest.fixture
def repository(db_session: AsyncSession) -> AvailabilityRuleRepository:
    return AvailabilityRuleRepository(db_session)


@pytest.fixture
async def owner(db_session: AsyncSession):
    """The account whose week is being replaced."""
    return await register_user(db_session, username="ada", email="ada@nexus.test")


@pytest.fixture
async def other(db_session: AsyncSession):
    """A second account, so the scope of the DELETE is pinned as well.

    A separate fixture rather than a parameter: whether the DELETE is scoped is a
    question this file asks, so the other account must exist independently of
    whether the test under it cares about the owner's week.
    """
    return await register_user(db_session, username="grace", email="grace@nexus.test")


def _rule(
    weekday: int, starts: str | time, ends: str | time, label: str | None = None
) -> dict[str, Any]:
    """Build one submitted rule as the mapping ``_rule_fields`` accepts.

    The times are parsed rather than written as ``time`` literals so a test reads
    as the payload a client would send, and so ``09:00`` and ``09:00:00`` cannot
    drift apart between two spellings of the same instant. An already-parsed
    ``time`` is accepted too, which is what lets a test seed its starting week
    from the same ``ORIGINAL_WEEK`` tuples its assertions compare against — the
    fixture and the expectation cannot then disagree by a typo.
    """

    def parse(value: str | time) -> time:
        return value if isinstance(value, time) else time.fromisoformat(value)

    return {
        "weekday": weekday,
        "starts_at": parse(starts),
        "ends_at": parse(ends),
        "label": label,
    }


async def _read(session: AsyncSession, owner_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Read an owner's stored week as plain tuples, ordered by weekday then start."""
    result = await session.execute(
        select(*_RULE_COLUMNS)
        .where(AvailabilityRule.owner_id == owner_id)
        .order_by(AvailabilityRule.weekday, AvailabilityRule.starts_at)
    )
    return [tuple(row) for row in result.all()]


async def _read_committed(engine: AsyncEngine, owner_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """Read an owner's week from a *separate connection*.

    The engine is ``NullPool``, so this is a fresh connection with its own
    transaction, and it sees committed data only. That is the whole point: a
    delete that was rolled back is invisible here and a delete that was committed
    is gone from here, while a read through the session that issued the delete
    would report the same thing either way.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        return await _read(session, owner_id)


# ----------------------------------------------------------------------
# The success path still stores the whole submitted week
# ----------------------------------------------------------------------


async def test_a_successful_replace_stores_exactly_the_rules_that_were_sent(
    repository, engine, owner
):
    """A replace of 2 rules by 3 leaves those 3, and none of the original 2.

    Derived from the fixture: ``ORIGINAL_WEEK`` is Monday 09:00-12:00 "core" and
    Tuesday 10:00-16:00 unlabelled. The payload is Monday 07:00-08:00 "early",
    Wednesday 09:00-17:00 and Friday 13:00-15:00 "admin". Wednesday and Friday
    appear in neither the original week nor Monday, and "early"/"admin" appear
    nowhere before, so the stored set can only be the submitted one — a partial
    write, a leftover row from the previous week, or a field left behind by the
    delete would each break the equality on a different line.
    """
    await repository.replace_for_user(owner.id, [_rule(*row) for row in ORIGINAL_WEEK])
    submitted = [
        _rule(0, "07:00", "08:00", "early"),
        _rule(2, "09:00", "17:00"),
        _rule(4, "13:00", "15:00", "admin"),
    ]

    stored = await repository.replace_for_user(owner.id, submitted)

    assert len(stored) == 3
    assert await _read_committed(engine, owner.id) == [
        (0, time(7, 0), time(8, 0), "early"),
        (2, time(9, 0), time(17, 0), None),
        (4, time(13, 0), time(15, 0), "admin"),
    ]


async def test_an_empty_payload_empties_the_week(repository, engine, owner):
    """Replacing with no rules leaves zero rules, which is a real answer.

    Derived from the fixture: ``ORIGINAL_WEEK`` is two rules, so "the user no
    longer works at all" must read as an empty list rather than as the previous
    week still standing — the delete-only direction of the same transaction.
    """
    await repository.replace_for_user(owner.id, [_rule(*row) for row in ORIGINAL_WEEK])

    stored = await repository.replace_for_user(owner.id, [])

    assert stored == []
    assert await _read_committed(engine, owner.id) == []


# ----------------------------------------------------------------------
# The failure path leaves the original week alone
# ----------------------------------------------------------------------


async def test_a_refused_replace_leaves_the_original_week_completely_intact(
    repository, engine, owner
):
    """A payload with a duplicate ``(weekday, starts_at)`` raises and destroys nothing.

    The payload is Wednesday 09:00-17:00 followed by Wednesday 09:00-12:00: two
    rules for the same weekday and the same start, which ``uq_availability_rules_
    owner_weekday_start`` refuses. Note the collision is *within* the payload —
    resubmitting an original rule is legal, because the delete has already
    removed the row it would have collided with.

    Expected, from the fixture's two rules: after the refusal the owner still has
    exactly Monday 09:00-12:00 "core" and Tuesday 10:00-16:00 unlabelled, seen
    from another connection, and none of the Wednesday rules. Before the fix this
    read returned an empty list — the week was gone and stayed gone.
    """
    await repository.replace_for_user(owner.id, [_rule(*row) for row in ORIGINAL_WEEK])

    with pytest.raises(IntegrityError):
        await repository.replace_for_user(
            owner.id,
            [_rule(2, "09:00", "17:00"), _rule(2, "09:00", "12:00")],
        )

    assert await _read_committed(engine, owner.id) == ORIGINAL_WEEK


async def test_no_part_of_a_refused_payload_is_ever_stored(repository, engine, owner):
    """The rules that *would* have been valid before the refused one are not kept.

    Derived from the fixture: the owner has two rules. The payload is Thursday
    06:00-07:00 "gym" — perfectly valid on its own — followed by the Wednesday
    duplicate the table refuses. A writer that inserted as it went would have
    committed "gym" before the constraint fired; a writer that committed the
    delete first would have left the owner with nothing. The only correct outcome
    is the original two rules, unchanged, with "gym" and both Wednesday rules
    absent, which is what this asserts from a second connection.
    """
    await repository.replace_for_user(owner.id, [_rule(*row) for row in ORIGINAL_WEEK])

    with pytest.raises(IntegrityError):
        await repository.replace_for_user(
            owner.id,
            [
                _rule(3, "06:00", "07:00", "gym"),
                _rule(2, "09:00", "17:00"),
                _rule(2, "09:00", "12:00"),
            ],
        )

    assert await _read_committed(engine, owner.id) == ORIGINAL_WEEK


async def test_a_window_that_does_not_advance_is_refused_without_destroying_anything(
    repository, engine, owner
):
    """A check-constraint refusal rolls back exactly like a unique one.

    ``ck_availability_rules_ends_after_starts`` refuses a window ending at or
    before its start. The payload is Saturday 12:00-12:00 — a zero-length window
    that covers no time — after Friday 16:00-18:00 "review", both valid in
    isolation. Expected: the owner's original two rules, and neither Friday rule
    stored. Pinning the second failure mode matters because a fix that only
    special-cased the unique violation would leave this one wiping the week.
    """
    await repository.replace_for_user(owner.id, [_rule(*row) for row in ORIGINAL_WEEK])

    with pytest.raises(IntegrityError):
        await repository.replace_for_user(
            owner.id, [_rule(4, "16:00", "18:00", "review"), _rule(5, "12:00", "12:00")]
        )

    assert await _read_committed(engine, owner.id) == ORIGINAL_WEEK


async def test_a_weekday_outside_zero_to_six_is_refused_without_destroying_anything(
    repository, engine, owner
):
    """``ck_availability_rules_weekday_range`` refuses weekday 7, and rolls back too.

    Derived from the fixture: two original rules. The payload adds Sunday-as-eight
    — weekday 7, which matches no date and is therefore a row that silently does
    nothing — so it must never be stored, and its presence in the payload must
    not cost the owner the week. Expected afterwards: the original two rules, in
    full, and no weekday-7 row anywhere.
    """
    await repository.replace_for_user(owner.id, [_rule(*row) for row in ORIGINAL_WEEK])

    with pytest.raises(IntegrityError):
        await repository.replace_for_user(owner.id, [_rule(7, "09:00", "17:00")])

    assert await _read_committed(engine, owner.id) == ORIGINAL_WEEK


# ----------------------------------------------------------------------
# The session and the following request survive the refusal
# ----------------------------------------------------------------------


async def test_the_same_session_is_usable_after_a_refused_replace(repository, engine, owner):
    """A rolled-back replace leaves the session in working order.

    SQLAlchemy resets the connection when a savepoint is rolled back, so the
    next statement is not answered with "current transaction is aborted". The
    session reads the original week — Monday 09:00-12:00 "core" and Tuesday
    10:00-16:00 — through the repository's own listing, and a following replace
    through the same session commits normally, leaving one rule: Friday
    15:00-17:00. Both halves matter: a session poisoned by the first failure
    would break every later request on that connection.
    """
    await repository.replace_for_user(owner.id, [_rule(*row) for row in ORIGINAL_WEEK])
    with pytest.raises(IntegrityError):
        await repository.replace_for_user(owner.id, [_rule(2, "09:00", "17:00")] * 2)

    listed = await repository.list_for_user(owner.id)

    assert [(rule.weekday, rule.starts_at, rule.ends_at, rule.label) for rule in listed] == (
        ORIGINAL_WEEK
    )

    await repository.replace_for_user(owner.id, [_rule(4, "15:00", "17:00")])

    assert await _read_committed(engine, owner.id) == [(4, time(15, 0), time(17, 0), None)]


async def test_a_second_put_replaces_the_first_puts_output(repository, engine, owner):
    """Two replaces in a row leave the second payload and nothing of the first.

    Derived from both payloads. The first stores three rules: Monday
    07:00-08:00 "early", Wednesday 09:00-17:00 and Friday 13:00-15:00 "admin".
    The second stores one: Monday 07:00-08:00 "early" again — a row the first
    payload legitimately reuses — and nothing else. So the expected final state is
    a single rule, which can only be reached if the second replace deleted the
    Wednesday and Friday rules as well as inserting nothing; a writer that
    skipped the delete, or that deleted by a key the reused rule collided with,
    would leave 2 or 3 rows instead of 1.
    """
    await repository.replace_for_user(
        owner.id,
        [
            _rule(0, "07:00", "08:00", "early"),
            _rule(2, "09:00", "17:00"),
            _rule(4, "13:00", "15:00", "admin"),
        ],
    )

    await repository.replace_for_user(owner.id, [_rule(0, "07:00", "08:00", "early")])

    assert await _read_committed(engine, owner.id) == [(0, time(7, 0), time(8, 0), "early")]


# ----------------------------------------------------------------------
# Scope and provenance
# ----------------------------------------------------------------------


async def test_a_refused_replace_does_not_reach_another_accounts_week(
    repository, engine, owner, other
):
    """The failed DELETE is scoped to the caller, and so is the failure.

    ``other`` holds one rule of its own: Thursday 08:00-09:00 unlabelled. After
    the owner's refused replace, ``other`` must still hold exactly that one rule
    and the owner exactly the original two. An unscoped ``DELETE ... WHERE
    owner_id = ?`` typed wrongly — or one that reached for every rule in the
    table — would empty both weeks at once, which no assertion about the owner's
    rows alone would catch.
    """
    await repository.replace_for_user(owner.id, [_rule(*row) for row in ORIGINAL_WEEK])
    await repository.replace_for_user(other.id, [_rule(3, "08:00", "09:00")])

    with pytest.raises(IntegrityError):
        await repository.replace_for_user(owner.id, [_rule(2, "09:00", "17:00")] * 2)

    assert await _read_committed(engine, owner.id) == ORIGINAL_WEEK
    assert await _read_committed(engine, other.id) == [(3, time(8, 0), time(9, 0), None)]


async def test_a_successful_replace_does_not_reach_another_accounts_week(
    repository, engine, owner, other
):
    """A *successful* replace is scoped too, so ownership is not only a failure case.

    The owner replaces their two rules with one — Wednesday 09:00-17:00 — while
    ``other`` keeps Thursday 08:00-09:00. Expected: the owner has exactly the one
    Wednesday rule and ``other`` exactly its one Thursday rule. Both accounts'
    payloads are single-row and distinct, so a leaked DELETE or a leaked insert
    would show up as a row moving between them rather than as a count.
    """
    await repository.replace_for_user(owner.id, [_rule(*row) for row in ORIGINAL_WEEK])
    await repository.replace_for_user(other.id, [_rule(3, "08:00", "09:00")])

    await repository.replace_for_user(owner.id, [_rule(2, "09:00", "17:00")])

    assert await _read_committed(engine, owner.id) == [(2, time(9, 0), time(17, 0), None)]
    assert await _read_committed(engine, other.id) == [(3, time(8, 0), time(9, 0), None)]


async def test_a_replaced_rule_carries_the_database_clock_not_the_applications(
    repository, db_session, owner
):
    """``created_at`` comes from the server's ``now()``, read back from the server.

    Both values are read from PostgreSQL in the same transaction — ``func.now()``
    alongside the stored column — so the comparison cannot drift with the machine
    running the suite. The stored timestamp must be no later than the clock that
    read it and no more than five seconds earlier, which is what a server default
    looks like and what a Python-side ``datetime.now()`` stamped by a different
    machine's clock would not be.

    The three rules of one replace are also asserted to carry the *same*
    timestamp, and that is the transaction reading its own work: PostgreSQL's
    ``now()`` is the transaction start time, so three rules written by one
    transaction share one instant. Three rows with three different timestamps
    would mean three transactions, and three transactions are exactly what the
    delete-then-insert bug used.
    """
    await repository.replace_for_user(owner.id, [_rule(*row) for row in ORIGINAL_WEEK])

    await repository.replace_for_user(
        owner.id,
        [
            _rule(0, "07:00", "08:00", "early"),
            _rule(2, "09:00", "17:00"),
            _rule(4, "15:00", "16:00"),
        ],
    )

    result = await db_session.execute(
        select(AvailabilityRule.created_at, func.now())
        .where(AvailabilityRule.owner_id == owner.id)
        .order_by(AvailabilityRule.weekday)
    )
    rows = result.all()
    database_now = rows[0][1]

    assert len(rows) == 3
    assert len({stamp for stamp, _ in rows}) == 1
    for stored_at, _ in rows:
        age = (database_now - stored_at).total_seconds()
        assert stored_at.tzinfo is not None
        assert 0 <= age <= 5
