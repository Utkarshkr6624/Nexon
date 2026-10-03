"""The career page reads ``career_evidence`` a bounded number of times, not once per page of it.

What was wrong
--------------
:meth:`CareerIntelligenceService._evidence_tally` produced the whole-set counts that
sit beside the evidence list and inside the summary and the feature vector by
**paging the entire table** through :meth:`CareerRepository.list_evidence` — one
round trip per 200 rows — and counting the rows in Python. Three methods want those
figures, so one page load of the career page swept the table three times, and every
one of those sweeps grew with the account: at 450 rows the list route alone spent
four statements, the summary six and the feature vector three.

Counting is not a job for Python here. The same figures are conditional aggregates
over the same rows, so :meth:`CareerRepository.evidence_tally` computes them in the
database and returns a handful of rows. The statement count is now **two whatever
the account holds**, and — a gain rather than a side effect — exact at every size:
the swept version stopped at ``career_max_evidence`` rows and under-counted past
it, so a large account was reported as smaller than it was.

What is asserted
----------------
Three things, in three tests:

1. **The figures are unchanged.** Every number the aggregate reports is compared
   against the hand-derived value for a fixed six-row fixture, including the two
   that are easy to get wrong — distinct skills versus skill-linked rows, and
   ``latest_evidence_on`` being ``None`` rather than today when nothing matched.
2. **The cost does not grow.** The whole page load — summary, list and features —
   is measured at 50 evidence rows and at 450, with a ``before_cursor_execute``
   listener on the engine, and the two totals are asserted equal *and* equal to the
   nine ``career_evidence`` statements derived in the test's docstring.
3. **The filters still filter.** A tally narrowed to one evidence type describes
   the narrowed set, which is the property a hand-rolled ``WHERE`` would most
   easily lose.

House style, following ``tests/test_career_service.py``
-------------------------------------------------------
* ``pytestmark = pytest.mark.integration`` — live PostgreSQL, truncated per test.
* Services are hand-wired in a module-level helper that mirrors ``app.api.deps``.
* Rows are written directly, bypassing the service, so a test's fixture cannot be
  perturbed by whatever a request schema requires this month.
* Every expected figure is derived in the test's own docstring.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.career import CareerEvidence
from app.models.enums import CareerEvidenceType, ProjectStatus
from app.repositories.activity import ActivityRepository
from app.repositories.career import CareerRepository
from app.repositories.developer import DeveloperRepository
from app.repositories.learning import LearningRepository
from app.repositories.project import ProjectRepository
from app.services.activity_service import ActivityService
from app.services.career import CareerIntelligenceService
from tests.analytics_fixtures import AnalyticsSeed, register_user

pytestmark = pytest.mark.integration

#: The two account sizes the cost is compared at. Four hundred and fifty rows is
#: three pages at the repository's own 200-row ceiling, which is where the swept
#: version started costing more than one statement per figure; fifty is one page,
#: where it cost the least. The invariant under test is that those two are equal.
SMALL_ACCOUNT = 50
LARGE_ACCOUNT = 450

#: How many statements touching ``career_evidence`` the whole career page load
#: costs, derived from the code:
#:
#: * ``summary`` — one page read for the windowed count (a second statement only
#:   when the window matched nothing, which is the state this fixture is in), plus
#:   the tally's two.
#: * ``list_evidence`` — the page itself, plus the tally's two.
#: * ``features`` — the tally's two, and nothing else.
#:
#: Four plus three plus two. The swept version cost six plus four plus three at 450
#: rows, and seven of those statements at fifty: the cost tracked the account.
PAGE_LOAD_EVIDENCE_STATEMENTS = 9


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _service(
    session: AsyncSession, *, settings: Settings | None = None
) -> CareerIntelligenceService:
    """A career service wired the way ``app.api.deps`` wires it.

    Every collaborator is the real one, for the reason
    ``tests/test_career_service.py`` gives: a stubbed repository would make a
    statement count meaningless.
    """
    return CareerIntelligenceService(
        CareerRepository(session),
        learning=LearningRepository(session),
        projects=ProjectRepository(session),
        developer=DeveloperRepository(session),
        activity=ActivityService(ActivityRepository(session)),
        settings=settings,
    )


async def _owner(session: AsyncSession, username: str = "ada"):
    """One account, inserted directly.

    Direct rather than through the API because these tests drive the service; a
    registered account also writes activity events, which would move nothing here
    but would make the fixture's provenance counts harder to read.
    """
    return await register_user(session, username=username)


async def _db_now(session: AsyncSession) -> datetime:
    """The database's clock, as an aware UTC instant.

    The same read :meth:`CareerIntelligenceService._now` performs. Seed dates are
    placed relative to this rather than to ``date.today()`` so a row the service
    will count sits inside the window the service will compute, whatever hour the
    suite runs at.
    """
    value = await session.scalar(select(func.now()))
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _seed_manual_evidence(session: AsyncSession, owner_id: uuid.UUID, count: int) -> None:
    """``count`` evidence rows the user typed by hand, all well outside the window.

    Three deliberate choices:

    * **``source`` is the default**, so every row is counted as manual and none
      names a subsystem — the provenance figures have somewhere to be wrong.
    * **No foreign key is set.** The deduplication index is partial over rows that
      name a source, so a run of identical identities with all three keys null is
      exactly the case a person is entitled to write. Rows that named a skill would
      be refused from the second one onwards.
    * **Every row is dated far outside the window.** The summary's windowed read
      then matches nothing and takes its documented extra statement, which is what
      makes the statement counts below derivable by hand rather than by a run.

    Args:
        session: The test session.
        owner_id: Whose evidence this is.
        count: How many rows to write.
    """
    old = date(2020, 1, 1)
    session.add_all(
        [
            CareerEvidence(
                id=uuid.uuid4(),
                user_id=owner_id,
                evidence_type=CareerEvidenceType.ACHIEVEMENT.value,
                title=f"Milestone {index}",
                occurred_on=old + timedelta(days=index % 300),
                source="manual",
            )
            for index in range(count)
        ]
    )
    await session.commit()


@contextmanager
def counting_statements(engine: Engine) -> Iterator[list[str]]:
    """Collect every statement the enclosed block sends to the database.

    ``before_cursor_execute`` on the engine, so the count is the whole cost of the
    call however deep inside a repository it is incurred — which is what grew when
    the tally paged the table. The list handed back is filled in *during* the block,
    so it is read after the block, never inside it.
    """
    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _record)


def evidence_reads(statements: list[str]) -> list[str]:
    """Only the statements that name ``career_evidence``.

    "How many statements touch ``career_evidence``" is the claim being made. The
    profile, the timeline, the learning totals and the git counts are a fixed cost
    of the page either way and say nothing about this defect.
    """
    return [text for text in statements if "career_evidence" in text]


def _render(statements: list[str]) -> str:
    """The counted statements, one per line, for an assertion message."""
    return "\n".join(f"    {' '.join(text.split())[:130]}" for text in statements)


# ---------------------------------------------------------------------------
# The figures are unchanged
# ---------------------------------------------------------------------------


async def test_the_evidence_tally_reports_the_same_figures_a_row_by_row_count_would_have(
    db_session,
):
    """Six rows, every figure derived from them by hand.

    The fixture and the arithmetic:

    ====================  ==========  ============  ==========================
    row                   type        source        what it carries
    ====================  ==========  ============  ==========================
    Rust refactor         skill_act.  ``skill``     skill ``S1``
    Rust study notes      achieve.    ``learning``  skill ``S2``
    Atlas shipped         achieve.    ``project``   project ``Atlas``
    Mentored two interns  achieve.    ``manual``    nothing
    Certificate           certific.   ``manual``    nothing
    Conference talk       achieve.    ``manual``    nothing
    ====================  ==========  ============  ==========================

    so: ``total`` 6; ``by_type`` = achievement 4, skill_activity 1, certification 1;
    ``by_source`` = manual 3, skill 1, learning 1, project 1; ``manual_count`` 3;
    ``skill_linked_count`` **2** (two rows name a skill); ``skills_with_evidence``
    **2** (two *distinct* skills — the difference is the point of the pair);
    ``linked_project_count`` 1; ``latest_evidence_on`` the most recent ``occurred_on``
    seeded.

    ``latest_evidence_on`` is checked against the database's own day rather than a
    literal, and every count against a hand arithmetic rather than against what the
    aggregate returned — a tally compared only with itself proves nothing.
    """
    owner = await _owner(db_session)
    today = (await _db_now(db_session)).date()
    skill_one = await LearningRepository(db_session).create_skill(owner.id, name="Rust")
    skill_two = await LearningRepository(db_session).create_skill(owner.id, name="Go")
    project = await AnalyticsSeed(db_session, owner).project(
        name="Atlas", status=ProjectStatus.COMPLETED.value
    )

    days = [1, 2, 3, 4, 5, 6]
    fixture = [
        (CareerEvidenceType.SKILL_ACTIVITY.value, "skill", skill_one.id),
        (CareerEvidenceType.ACHIEVEMENT.value, "learning", skill_two.id),
        (CareerEvidenceType.ACHIEVEMENT.value, "project", None),
        (CareerEvidenceType.ACHIEVEMENT.value, "manual", None),
        (CareerEvidenceType.CERTIFICATION.value, "manual", None),
        (CareerEvidenceType.ACHIEVEMENT.value, "manual", None),
    ]
    for (evidence_type, source, skill_id), offset in zip(fixture, days, strict=True):
        db_session.add(
            CareerEvidence(
                id=uuid.uuid4(),
                user_id=owner.id,
                evidence_type=evidence_type,
                title=f"Evidence {offset}",
                occurred_on=today - timedelta(days=offset),
                skill_id=skill_id,
                project_id=project.id if source == "project" else None,
                source=source,
            )
        )
    await db_session.commit()

    tally = await CareerRepository(db_session).evidence_tally(owner.id)

    assert tally["total"] == 6
    assert tally["by_type"] == {"achievement": 4, "skill_activity": 1, "certification": 1}
    assert tally["by_source"] == {"manual": 3, "skill": 1, "learning": 1, "project": 1}
    assert tally["manual_count"] == 3
    # Two rows name a skill; they name two different ones.
    assert tally["skill_linked_count"] == 2
    assert tally["skills_with_evidence"] == 2
    assert tally["linked_project_count"] == 1
    # The most recent of offsets 1..6 days back is one day back.
    assert tally["latest_evidence_on"] == today - timedelta(days=1)


async def test_an_account_with_no_evidence_reports_zeroes_and_a_missing_latest_date(db_session):
    """An empty set is zeros and ``None`` — never today's date, never a missing key.

    No row is seeded, so the table was searched and found empty. ``by_type`` and
    ``by_source`` are ``{}`` rather than zero-filled: the type vocabulary is closed
    and the schema completes it, while the source vocabulary is open and a
    subsystem that has never run has no band to appear in.
    ``latest_evidence_on`` is ``None`` because the day of the most recent evidence
    is an **absent measurement** — reporting today would claim the user did
    something, on a day they did nothing.
    """
    owner = await _owner(db_session)

    tally = await CareerRepository(db_session).evidence_tally(owner.id)

    assert tally["total"] == 0
    assert tally["by_type"] == {}
    assert tally["by_source"] == {}
    assert tally["manual_count"] == 0
    assert tally["skills_with_evidence"] == 0
    assert tally["linked_project_count"] == 0
    assert tally["latest_evidence_on"] is None


async def test_a_narrowed_tally_describes_the_narrowed_set_and_not_the_whole_table(db_session):
    """The aggregate is filtered by exactly the clauses the page is filtered by.

    Four rows are seeded: three ``ACHIEVEMENT`` and one ``CERTIFICATION``, all
    manual. Narrowed to ``CERTIFICATION`` the tally must describe **one** row —
    ``total`` 1, ``by_type`` one band, ``by_source`` one band — which is the
    property a hand-written ``WHERE`` would most easily lose, and the one that makes
    the header beside a filtered list describe the list under it.

    The narrowed figure is also compared with the page the route returns for the
    same filter: a list and the counts printed beside it must be about the same set.
    """
    owner = await _owner(db_session)
    today = (await _db_now(db_session)).date()
    for index, evidence_type in enumerate(
        [
            CareerEvidenceType.ACHIEVEMENT.value,
            CareerEvidenceType.ACHIEVEMENT.value,
            CareerEvidenceType.ACHIEVEMENT.value,
            CareerEvidenceType.CERTIFICATION.value,
        ]
    ):
        db_session.add(
            CareerEvidence(
                id=uuid.uuid4(),
                user_id=owner.id,
                evidence_type=evidence_type,
                title=f"Row {index}",
                occurred_on=today - timedelta(days=index + 1),
                source="manual",
            )
        )
    await db_session.commit()

    service = _service(db_session)
    tally = await CareerRepository(db_session).evidence_tally(
        owner.id, evidence_type=CareerEvidenceType.CERTIFICATION.value
    )
    page = await service.list_evidence(
        owner=owner, evidence_type=CareerEvidenceType.CERTIFICATION.value
    )

    assert tally["total"] == 1
    assert tally["by_type"] == {"certification": 1}
    assert tally["by_source"] == {"manual": 1}
    assert page.total == tally["total"]
    assert page.by_type["certification"] == 1
    assert page.by_source["manual"] == 1
    assert [item.evidence_type for item in page.items] == [
        CareerEvidenceType.CERTIFICATION.value
    ], "the page must show the narrowed set, not the newest row overall"


# ---------------------------------------------------------------------------
# The cost does not grow
# ---------------------------------------------------------------------------


async def test_one_career_page_load_reads_the_evidence_table_the_same_number_of_times_at_fifty_rows_and_at_four_hundred_and_fifty(
    db_session,
):
    """The page load's evidence reads are a constant, not a function of the account.

    ``summary``, ``list_evidence`` and ``features`` are called in sequence against
    two accounts — one holding fifty evidence rows, one holding four hundred and
    fifty — and the statements naming ``career_evidence`` are counted on each call.
    Both totals are asserted equal to each other **and** to
    :data:`PAGE_LOAD_EVIDENCE_STATEMENTS` (nine), derived read by read in that
    constant's comment.

    The two accounts differ only in row count: same account shape, same fixture
    routine, same manual rows with no foreign keys, all dated far outside the
    summary window. Before the fix the swept version cost **thirteen** statements at
    450 rows and **seven** at fifty — the difference being purely how many 200-row
    pages each tally had to walk. A regression that puts the page walk back makes
    the two totals differ again, and this test fails.
    """
    engine: Engine = db_session.get_bind()

    small_owner = await _owner(db_session, "small")
    await _seed_manual_evidence(db_session, small_owner.id, SMALL_ACCOUNT)
    small, small_statements = await _page_load_statements(db_session, engine, small_owner)

    large_owner = await _owner(db_session, "large")
    await _seed_manual_evidence(db_session, large_owner.id, LARGE_ACCOUNT)
    large, large_statements = await _page_load_statements(db_session, engine, large_owner)

    assert small == PAGE_LOAD_EVIDENCE_STATEMENTS, (
        f"fifty rows cost {small} evidence statements, not {PAGE_LOAD_EVIDENCE_STATEMENTS}:\n"
        + _render(small_statements)
    )
    assert large == PAGE_LOAD_EVIDENCE_STATEMENTS, (
        f"four hundred and fifty rows cost {large} evidence statements, not "
        f"{PAGE_LOAD_EVIDENCE_STATEMENTS}:\n" + _render(large_statements)
    )
    assert small == large, "the page load's cost must not depend on how much evidence there is"


async def test_the_timeline_sweep_is_still_bounded_and_the_evidence_tally_is_not(db_session):
    """One tally is two statements however many evidence rows exist.

    A narrower claim than the page load above, stated separately so a future
    change to any one of the three routes cannot quietly re-introduce a walk: the
    evidence tally itself, called directly, costs exactly two statements at fifty
    rows and at four hundred and fifty.

    **Two** is derived: one ``UNION ALL`` of two grouped selects for the two band
    vocabularies, and one aggregate row for the total, the manual count, the two
    distinct counts and the latest date. The swept version this replaced issued one
    statement per two hundred rows — three at 450, one at 50.
    """
    engine: Engine = db_session.get_bind()
    repository = CareerRepository(db_session)

    small_owner = await _owner(db_session, "small")
    await _seed_manual_evidence(db_session, small_owner.id, SMALL_ACCOUNT)
    with counting_statements(engine) as small_all:
        await repository.evidence_tally(small_owner.id)
    small_statements = evidence_reads(small_all)

    large_owner = await _owner(db_session, "large")
    await _seed_manual_evidence(db_session, large_owner.id, LARGE_ACCOUNT)
    with counting_statements(engine) as large_all:
        await repository.evidence_tally(large_owner.id)
    large_statements = evidence_reads(large_all)

    assert len(small_statements) == 2, _render(small_statements)
    assert len(large_statements) == 2, _render(large_statements)


# ---------------------------------------------------------------------------
# Test-only plumbing
# ---------------------------------------------------------------------------


async def _page_load_statements(
    session: AsyncSession, engine: Engine, owner
) -> tuple[int, list[str]]:
    """Count the ``career_evidence`` statements of one whole career page load.

    The three calls are made on **one** service instance, which is what a page
    load costs when it is served by one process: the summary, the list and the
    feature vector are three reads of the same set and are counted together.

    Returns:
        How many statements named ``career_evidence``, and the statements
        themselves so a failing assertion can print them.
    """
    service = _service(session)
    with counting_statements(engine) as statements:
        await service.summary(owner=owner)
        await service.list_evidence(owner=owner)
        await service.features(owner=owner)
    reads = evidence_reads(statements)
    return len(reads), reads
