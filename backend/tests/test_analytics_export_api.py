"""CSV export over HTTP: the manifest, the download, and what the file promises.

**Every test here requires a live PostgreSQL and has been executed.** They are
marked ``integration``.

Two routes are covered: ``GET /analytics/export`` (the manifest — which datasets
exist and what columns each carries) and ``GET /analytics/export.csv`` (the file).
They are deliberately tested *together* because the second is meaningless without
the first: a download whose shape a client had to discover by trial and error is
not a contract, and the manifest is the thing that makes it one. Every assertion
below therefore compares the downloaded body against the manifest's declared
columns before it compares anything against a fixture.

The CSV is parsed with the stdlib :mod:`csv` module and compared cell by cell
rather than by substring. A substring assertion on a CSV passes while every
column after the match is shifted by a stray comma, which is precisely the
failure RFC 4180 quoting exists to prevent — so the tests that check quoting
check it by *parsing*, where an unbalanced quote shows up as a wrong row count.

Isolation is asserted, not assumed. An export is the one analytics surface that
leaves the database as a file the user keeps, so "did the second account get the
first account's rows" is the question worth spending tests on.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from tests.analytics_fixtures import DAY, AnalyticsSeed, at, seeded_client

pytestmark = pytest.mark.integration

#: The three-day window every export test asks for. Three days rather than one
#: because a single-day window cannot tell "the filter worked" apart from "there
#: happened to be nothing else", and not seven because a seven-day window seeds
#: seven rows of zeros to keep asserting against.
WINDOW_START = DAY
WINDOW_END = DAY + timedelta(days=2)
WINDOW = f"start_date={WINDOW_START}&end_date={WINDOW_END}"

#: The datasets ``/analytics/export`` must advertise. Spelled out here rather
#: than imported from ``CSV_DATASETS``: the whole point of the manifest is that a
#: client learns the list from the wire, so a test that read the source of truth
#: it is meant to be checking would pass even if the route were wrong.
EXPECTED_DATASETS = ("daily_metrics", "task_performance", "work_sessions")

#: The 12 counter columns ``daily_metrics`` stores, in the alphabetical order
#: ``METRIC_COLUMNS`` is built in — and therefore the order the CSV header
#: carries them. ``metric_date`` is prepended and ``updated_at`` appended.
EXPECTED_DAILY_COLUMNS = (
    "actual_minutes",
    "calendar_events",
    "knowledge_events",
    "planned_minutes",
    "projects_touched",
    "tasks_blocked",
    "tasks_cancelled",
    "tasks_completed",
    "tasks_created",
    "tasks_overdue",
    "tasks_rescheduled",
    "work_sessions",
)


def rows_of(body: str) -> list[list[str]]:
    """Parse a CSV document into rows of raw cells.

    ``newline=""`` on the buffer is load-bearing: without it the default
    universal-newline translation rewrites the CRLF record separators, and a file
    whose embedded newline was normalised can no longer prove the embedded
    newline survived the round trip.
    """
    return list(csv.reader(io.StringIO(body, newline="")))


def instant(cell: str) -> datetime:
    """Read a CSV cell as the instant it denotes.

    Compared against an aware :func:`~tests.analytics_fixtures.at` value rather
    than against a literal ISO string because the offset a ``timestamptz`` reads
    back with is the *database session's* zone, not a property of the stored
    value. Comparing aware datetimes compares the instants, which is what the
    column actually holds; a test that asserted ``"+00:00"`` would be asserting
    the host's timezone configuration instead of the analytics engine.
    """
    return datetime.fromisoformat(cell)


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------


async def seed_window(seed: AnalyticsSeed) -> dict[str, Any]:
    """Seed three days of activity and return the ids the tests assert against.

    Every figure below is derived by hand from the rows this function writes; the
    tests name the arithmetic inline so a reader can check it without running
    anything. Two of the shapes are deliberately awkward — a project name
    containing a comma, a task title containing quotes and a newline — because a
    fixture of tidy one-word labels would let a CSV writer that ignored RFC 4180
    pass every other test in this file.
    """
    # Project names carry the awkward characters: "Alpha, Platform" needs
    # quoting, 'Beta "Quoted"' needs a doubled quote.
    alpha = await seed.project(name="Alpha, Platform")
    beta = await seed.project(name='Beta "Quoted"')

    # -- day 1 ---------------------------------------------------------------
    # Two tasks created and completed on the day, both due the same day, so both
    # are on time and neither counts as overdue.
    await seed.completed_task(day=DAY, project_id=alpha.id, estimated_minutes=60, actual_minutes=50)
    await seed.completed_task(day=DAY, project_id=alpha.id, estimated_minutes=30, actual_minutes=45)
    # Two more created on day 1 and still open: one with no deadline, one due on
    # day 1 and therefore overdue.
    open_task = await seed.task(project_id=alpha.id, created_at=at(DAY, 14))
    await seed.task(project_id=alpha.id, created_at=at(DAY, 14), due_date=DAY)
    # Day-1 tasks_created is therefore 4, tasks_completed 2, tasks_overdue 1.
    await seed.work_session(
        day=DAY,
        minutes=90,
        task_id=open_task.id,
        project_id=alpha.id,
        start_hour=9,
        estimated_minutes=120,
    )
    # A cancelled session on the same day. Excluded from both planned and actual
    # minutes, so day 1's totals stay 90/90 and the session count stays 1.
    await seed.work_session(
        day=DAY, minutes=60, project_id=alpha.id, start_hour=13, status="cancelled"
    )
    await seed.calendar_event(day=DAY, project_id=alpha.id, start_hour=11, minutes=45)
    await seed.activity("task_blocked", day=DAY, project_id=alpha.id)
    await seed.activity("task_rescheduled", day=DAY, project_id=alpha.id)
    # The one knowledge event on day 1 names the *second* project, which is what
    # makes projects_touched 2 rather than 1: it is a distinct count over the
    # feed's project ids, and the two block/reschedule rows on ``alpha`` are
    # still only one project.
    await seed.activity("note_created", day=DAY, project_id=beta.id)
    # A note row with *no* event behind it. knowledge_events reads the feed, not
    # the notes table, so day 1's figure must stay 1 — a fixture that skipped this
    # would let a table-count implementation pass unnoticed.
    await seed.note(day=DAY)

    # -- day 2 ---------------------------------------------------------------
    # Completed on day 2 but due on day 1: the daily series counts it as overdue
    # on the day it was due, which takes day 1's tasks_overdue to 2 and day 2's
    # to 0.
    late = await seed.completed_task(
        day=DAY + timedelta(days=1),
        project_id=alpha.id,
        due_date=DAY,
        estimated_minutes=20,
        actual_minutes=25,
    )
    # Created on day 1, cancelled on day 2. Cancellation has no timestamp of its
    # own and buckets on updated_at, so day 2's tasks_cancelled is 1 while day 1's
    # tasks_created reaches 5.
    await seed.task(
        project_id=beta.id,
        status="cancelled",
        created_at=at(DAY, 15),
        updated_at=at(DAY + timedelta(days=1), 11),
    )
    await seed.work_session(
        day=DAY + timedelta(days=1), minutes=45, task_id=late.id, project_id=beta.id, start_hour=14
    )
    # A knowledge event with no project id: it counts as a knowledge interaction
    # but contributes nothing to projects_touched, which counts distinct
    # *non-null* project ids.
    await seed.activity("note_updated", day=DAY + timedelta(days=1))

    # -- day 3 ---------------------------------------------------------------
    # ``tasks.project_id`` is NOT NULL, so there is no project-less task to seed
    # on the last day — the export's ``project_name`` column can only be empty for
    # a row whose project has been deleted out from under it, which the fixture
    # never produces.
    await seed.task(project_id=alpha.id, created_at=at(DAY + timedelta(days=2), 8))

    return {"alpha": alpha, "beta": beta, "open_task": open_task, "late": late}


async def export(client, auth: dict[str, str], dataset: str, extra: str = "") -> Any:
    """Download one dataset over the canonical window and return the response."""
    query = f"dataset={dataset}&{WINDOW}" + (f"&{extra}" if extra else "")
    return await client.get(f"/api/v1/analytics/export.csv?{query}", headers=auth)


async def rebuild(client, auth: dict[str, str]) -> None:
    """Compute ``daily_metrics`` for the window; an export reads the table."""
    response = await client.post(f"/api/v1/analytics/rebuild?{WINDOW}", headers=auth)
    assert response.status_code == 202, response.text


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------


async def test_manifest_lists_every_dataset_with_its_declared_columns(client, db_session):
    """``GET /analytics/export`` names all three datasets and their columns.

    The manifest is the contract an importer is written against, so this asserts
    the full dataset list and the full column list of each — a manifest that had
    dropped a column would still "list the datasets" and still pass a weaker test.
    """
    _, auth = await seeded_client(client, db_session)

    response = await client.get("/api/v1/analytics/export", headers=auth)

    assert response.status_code == 200, response.text
    manifest = response.json()
    assert manifest["datasets"] == list(EXPECTED_DATASETS)
    assert manifest["content_type"] == "text/csv"
    assert set(manifest["columns"]) == set(EXPECTED_DATASETS)
    assert manifest["columns"]["daily_metrics"] == [
        "metric_date",
        *EXPECTED_DAILY_COLUMNS,
        "updated_at",
    ]
    assert manifest["columns"]["task_performance"] == [
        "task_id",
        "project_name",
        "title",
        "status",
        "priority",
        "estimated_minutes",
        "actual_minutes",
        "due_date",
        "created_at",
        "completed_at",
        "estimate_error_minutes",
    ]
    assert manifest["columns"]["work_sessions"] == [
        "session_id",
        "task_id",
        "project_id",
        "scheduled_start",
        "scheduled_end",
        "actual_start",
        "actual_end",
        "estimated_minutes",
        "actual_minutes",
        "status",
    ]
    # Every dataset maps to its own columns, and none of them is empty: a client
    # building an importer has to be able to iterate this without a special case.
    for dataset, columns in manifest["columns"].items():
        assert columns, dataset
        assert all(column for column in columns), dataset


async def test_manifest_is_identical_for_every_account(client, db_session):
    """The manifest describes the file format, not the data, so it is not per-user.

    Two accounts with different data must be told the same column lists. If the
    manifest were ever built from the caller's rows, a client that fetched it
    before its first upload would get an empty column list.
    """
    seed_a, auth_a = await seeded_client(client, db_session, username="ada", email="ada@nexus.test")
    seed_b, auth_b = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    ada_project = await seed_a.project()
    grace_project = await seed_b.project()
    await seed_a.task(project_id=ada_project.id, title="only ada has this")
    await seed_b.task(project_id=grace_project.id, title="only grace has this")

    first = await client.get("/api/v1/analytics/export", headers=auth_a)
    second = await client.get("/api/v1/analytics/export", headers=auth_b)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


# ---------------------------------------------------------------------------
# It is a file download, not a JSON envelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("dataset", EXPECTED_DATASETS)
async def test_export_is_a_csv_download_with_a_filename(client, db_session, dataset):
    """Each dataset in the manifest is downloadable and comes back as a file.

    ``Content-Disposition`` with a filename is the whole difference between "a
    browser saves this file" and "a browser navigates to a page of text". The
    filename also carries the dataset name and the resolved window, so a user who
    downloads six exports can tell them apart without opening any of them.
    """
    seed, auth = await seeded_client(client, db_session)
    await seed_window(seed)
    await rebuild(client, auth)

    response = await export(client, auth, dataset)

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    disposition = response.headers["Content-Disposition"]
    assert disposition == (
        f'attachment; filename="nexus-{dataset}-{WINDOW_START.isoformat()}'
        f'-{WINDOW_END.isoformat()}.csv"'
    )
    # A JSON envelope would parse; a CSV must not. Asserted through the parser
    # the tests below use rather than by a string prefix, so the check keeps
    # meaning if the header order ever changes.
    assert rows_of(response.text)[0] is not None


async def test_downloaded_header_matches_the_manifest_exactly(client, db_session):
    """The first row of every file is the manifest's column list, in its order.

    This is the pairing the whole feature rests on: the manifest is what a client
    builds an importer against, so a download whose header has drifted from it
    silently corrupts every import that was written correctly.
    """
    seed, auth = await seeded_client(client, db_session)
    await seed_window(seed)
    await rebuild(client, auth)
    manifest = (await client.get("/api/v1/analytics/export", headers=auth)).json()

    for dataset in EXPECTED_DATASETS:
        response = await export(client, auth, dataset)
        header = rows_of(response.text)[0]
        assert header == manifest["columns"][dataset], dataset


async def test_empty_window_returns_the_header_and_no_data_rows(client, db_session):
    """A brand-new account can still download, open and diff a file.

    "No data" is an answer, not an error: a 200 with a header row and zero data
    rows is the only shape from which a client can build an importer before the
    user has recorded anything.
    """
    _, auth = await seeded_client(client, db_session)

    response = await export(client, auth, "task_performance")

    assert response.status_code == 200, response.text
    assert response.headers["X-Nexus-Row-Count"] == "0"
    parsed = rows_of(response.text)
    assert len(parsed) == 1
    assert parsed[0][0] == "task_id"


# ---------------------------------------------------------------------------
# RFC 4180
# ---------------------------------------------------------------------------


async def test_line_endings_are_crlf_everywhere(client, db_session):
    r"""Every record separator is CRLF, including the last one.

    RFC 4180 mandates CRLF, and the alternative implementations get wrong
    silently: a single trailing ``\n`` is invisible in every spreadsheet and in
    most viewers, and only a byte-level check catches it.
    """
    seed, auth = await seeded_client(client, db_session)
    await seed_window(seed)
    await rebuild(client, auth)

    response = await export(client, auth, "task_performance")

    body = response.text
    assert body.endswith("\r\n")
    assert "\n" not in body.replace("\r\n", "")


async def test_fields_with_commas_quotes_and_newlines_survive_a_round_trip(client, db_session):
    """A title carrying a comma, quotes and a newline parses back intact.

    This is the test that a substring assertion cannot make. A writer that joined
    on commas without quoting would produce a row with the wrong *width*, and the
    only way to see that is to parse the file and compare the cells.
    """
    seed, auth = await seeded_client(client, db_session)
    awkward = 'Fix, "urgent"\nsecond line'
    await seed.task(
        title=awkward, created_at=at(DAY, 8), project_id=(await seed.project(name="A, B")).id
    )

    response = await export(client, auth, "task_performance")

    parsed = rows_of(response.text)
    assert len(parsed) == 2, parsed
    header, row = parsed
    assert len(row) == len(header)
    # The comma, both quotes and the embedded newline are all still there, and
    # the columns after the title are still in the positions the manifest named.
    assert row[header.index("title")] == awkward
    assert row[header.index("project_name")] == "A, B"
    assert row[header.index("status")] == "todo"
    assert row[header.index("estimated_minutes")] == ""


async def test_quotes_inside_a_quoted_field_are_doubled(client, db_session):
    """The RFC 4180 escaping of a literal quote is a doubled quote.

    Asserted on the bytes rather than only through the parser: a reader that
    tolerated an undoubled quote would still hand back the right string here,
    while a writer that produced one would break every other CSV reader.
    """
    seed, auth = await seeded_client(client, db_session)
    title = 'say "hello"'
    project = await seed.project()
    await seed.task(project_id=project.id, title=title, created_at=at(DAY, 8))

    response = await export(client, auth, "task_performance")

    assert '"say ""hello"""' in response.text
    assert rows_of(response.text)[1][2] == title


@pytest.mark.parametrize("payload", ["=1+1", "+1+1", "-1+1", "@SUM(A1)"])
async def test_formula_prefix_is_neutralised_before_it_reaches_a_sheet(client, db_session, payload):
    """A cell starting with ``=``, ``+``, ``-`` or ``@`` must not be emitted raw.

    An export is the one analytics surface that leaves the database as a file the
    user double-clicks, so a task titled ``=HYPERLINK(...)`` would otherwise be
    written verbatim and then *evaluated* by Excel, LibreOffice or Sheets when the
    file is opened. RFC 4180 quoting does not help: none of these payloads
    contains a comma, a quote or a newline, so the leading character reaches the
    cell intact.

    ``_csv_safe`` prefixes an apostrophe, which forces text and leaves the value
    the user sees unchanged. Only the leading character is considered, so an
    ordinary title like ``C++ tutorial`` is not rewritten.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    await seed.task(project_id=project.id, title=payload, created_at=at(DAY, 8))

    response = await export(client, auth, "task_performance")

    parsed = rows_of(response.text)
    emitted = parsed[1][parsed[0].index("title")]
    assert not emitted.startswith(("=", "+", "-", "@")), emitted


# ---------------------------------------------------------------------------
# Exact cell values
# ---------------------------------------------------------------------------


async def test_daily_metrics_csv_carries_the_exact_rebuilt_aggregates(client, db_session):
    """Every cell of every day, checked against the arithmetic by hand.

    Day 1: 4 created (two completed, two still open) + the cancelled one seeded
    for day 2 = 5; 2 completed; 2 overdue (the open one due today, and the one
    due today but finished tomorrow); 0 cancelled (that task's last write is on
    day 2); 1 blocked, 1 rescheduled, 1 knowledge event; 90 planned and 90 actual
    minutes from the one live session (the cancelled 60 never counts); 1 session;
    1 calendar event; 2 projects touched.

    Day 2: 1 created, 1 completed, 1 cancelled, 45/45 minutes, 1 session, 1
    knowledge event, 0 projects touched (its event names no project).

    Day 3: 1 created, nothing else.
    """
    seed, auth = await seeded_client(client, db_session)
    await seed_window(seed)
    await rebuild(client, auth)

    response = await export(client, auth, "daily_metrics")

    assert response.status_code == 200, response.text
    parsed = rows_of(response.text)
    header, *data = parsed
    assert header == ["metric_date", *EXPECTED_DAILY_COLUMNS, "updated_at"]
    assert response.headers["X-Nexus-Row-Count"] == "3"

    expected = [
        ("2026-01-05", ["90", "1", "1", "90", "2", "1", "0", "2", "5", "2", "1", "1"]),
        ("2026-01-06", ["45", "0", "1", "45", "0", "0", "1", "1", "1", "0", "0", "1"]),
        ("2026-01-07", ["0", "0", "0", "0", "0", "0", "0", "0", "1", "0", "0", "0"]),
    ]
    assert len(data) == len(expected)
    for row, (metric_date, counters) in zip(data, expected, strict=True):
        assert len(row) == len(header), row
        assert row[0] == metric_date
        assert row[1 : 1 + len(EXPECTED_DAILY_COLUMNS)] == counters, metric_date
        # ``updated_at`` is written by the database's own clock, so it is checked
        # as an instant that exists rather than against a value a fixture could
        # have pinned.
        assert instant(row[-1]) <= datetime.now(UTC)


async def test_task_performance_csv_carries_the_exact_task_rows(client, db_session):
    """Every task cell, including the two that make the design choices visible.

    ``estimate_error_minutes`` is ``actual - estimated``, so the day-1 pair gives
    ``-10`` (under by ten) and ``+15`` (over by fifteen) from one fixture. A task
    with no estimate yields an **empty** cell there rather than ``0``: "not
    estimated" and "estimated perfectly" are different facts, and a CSV that
    cannot tell them apart is a CSV nobody can trust.
    """
    seed, auth = await seeded_client(client, db_session)
    ids = await seed_window(seed)

    response = await export(client, auth, "task_performance")

    assert response.status_code == 200, response.text
    parsed = rows_of(response.text)
    header, *data = parsed
    assert response.headers["X-Nexus-Row-Count"] == str(len(data))

    completed = [row for row in data if row[header.index("status")] == "completed"]
    assert len(completed) == 3

    def cells(row: list[str]) -> dict[str, str]:
        return dict(zip(header, row, strict=True))

    # The first completed task of day 1: estimated 60, took 50, so -10.
    first = next(row for row in completed if row[header.index("actual_minutes")] == "50")
    first_cells = cells(first)
    assert first_cells["estimated_minutes"] == "60"
    assert first_cells["estimate_error_minutes"] == "-10"
    assert first_cells["due_date"] == "2026-01-05"
    assert instant(first_cells["created_at"]) == at(DAY, 9)
    assert instant(first_cells["completed_at"]) == at(DAY, 12)
    assert first_cells["priority"] == "medium"
    assert first_cells["project_name"] == "Alpha, Platform"

    # Its sibling: estimated 30, took 45, so +15.
    second = next(row for row in completed if row[header.index("actual_minutes")] == "45")
    assert cells(second)["estimate_error_minutes"] == "15"

    # The late one: due on day 1, finished on day 2, estimated 20, took 25.
    late = next(row for row in completed if row[header.index("actual_minutes")] == "25")
    late_cells = cells(late)
    assert late_cells["estimate_error_minutes"] == "5"
    assert late_cells["due_date"] == "2026-01-05"
    assert instant(late_cells["created_at"]) == at(DAY + timedelta(days=1), 9)
    assert instant(late_cells["completed_at"]) == at(DAY + timedelta(days=1), 12)
    assert late_cells["task_id"] == str(ids["late"].id)

    # The day-3 task has no estimate, no deadline and no completion: three empty
    # cells where a zero would have claimed something was recorded.
    newest = next(
        row
        for row in data
        if instant(row[header.index("created_at")]).astimezone(UTC)
        == at(DAY + timedelta(days=2), 8)
    )
    newest_cells = cells(newest)
    assert newest_cells["status"] == "todo"
    assert newest_cells["estimated_minutes"] == ""
    assert newest_cells["estimate_error_minutes"] == ""
    assert newest_cells["completed_at"] == ""
    assert newest_cells["due_date"] == ""

    # Every row is a full row, and the project names carried through the outer
    # join are exactly the two seeded. There is no empty cell among them:
    # ``tasks.project_id`` is NOT NULL with a foreign key to ``projects``, so the
    # join always matches and an exported task can never be unattributed.
    assert {row[header.index("project_name")] for row in data} == {
        "Alpha, Platform",
        'Beta "Quoted"',
    }
    assert {row[header.index("status")] for row in data} == {"completed", "todo", "cancelled"}


async def test_work_sessions_csv_carries_the_exact_session_rows(client, db_session):
    """Session rows verbatim, cancelled one included.

    A cancelled session is deliberately kept here where every aggregate drops it:
    an export is a record of what the user did, and a booked-then-cancelled slot
    is exactly the row someone checks when auditing their own week. Its missing
    ``estimated_minutes`` renders as an empty cell, and a session with no task or
    project renders two more.
    """
    seed, auth = await seeded_client(client, db_session)
    ids = await seed_window(seed)

    response = await export(client, auth, "work_sessions")

    assert response.status_code == 200, response.text
    parsed = rows_of(response.text)
    header, *data = parsed
    assert len(data) == 3
    assert response.headers["X-Nexus-Row-Count"] == "3"

    def cells(row: list[str]) -> dict[str, str]:
        return dict(zip(header, row, strict=True))

    live = cells(data[0])
    assert live["status"] == "completed"
    assert live["estimated_minutes"] == "120"
    assert live["actual_minutes"] == "90"
    assert instant(live["scheduled_start"]) == at(DAY, 9)
    assert instant(live["scheduled_end"]) == at(DAY, 9) + timedelta(minutes=90)
    assert instant(live["actual_start"]) == at(DAY, 9)
    assert instant(live["actual_end"]) == at(DAY, 9) + timedelta(minutes=90)
    assert live["task_id"] == str(ids["open_task"].id)
    assert live["project_id"] == str(ids["alpha"].id)

    cancelled = cells(data[1])
    assert cancelled["status"] == "cancelled"
    assert cancelled["task_id"] == ""
    assert cancelled["estimated_minutes"] == ""
    assert cancelled["actual_minutes"] == "60"
    assert instant(cancelled["scheduled_start"]) == at(DAY, 13)

    second = cells(data[2])
    assert second["status"] == "completed"
    assert second["actual_minutes"] == "45"
    assert instant(second["scheduled_start"]) == at(DAY + timedelta(days=1), 14)
    assert second["project_id"] == str(ids["beta"].id)


async def test_rebuild_is_idempotent_and_the_export_follows_it(client, db_session):
    """Running the rebuild twice leaves the export byte-identical.

    The upsert is keyed on ``UNIQUE (user_id, metric_date)``, so a recompute is a
    rewrite rather than an append. A CSV that grew a row per rebuild would be the
    clearest possible proof that analytics double-counts, which the spec forbids.
    """
    seed, auth = await seeded_client(client, db_session)
    await seed_window(seed)
    await rebuild(client, auth)
    first = (await export(client, auth, "daily_metrics")).text
    await rebuild(client, auth)
    second = (await export(client, auth, "daily_metrics")).text

    # ``updated_at`` is the one column a second upsert legitimately rewrites, so
    # the comparison is over every cell except that one.
    def without_updated(body: str) -> list[list[str]]:
        return [row[:-1] for row in rows_of(body)]

    assert without_updated(second) == without_updated(first)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_unknown_dataset_is_a_422_in_the_shared_error_envelope(
    client, db_session, assert_error_envelope
):
    """A dataset nobody declared is refused, not silently defaulted.

    Falling back to the default dataset would hand the user a file full of the
    wrong rows under the name they asked for, which is the failure the manifest
    exists to prevent.
    """
    seed, auth = await seeded_client(client, db_session)
    project = await seed.project()
    await seed.task(project_id=project.id)

    response = await export(client, auth, "sessions")

    error = assert_error_envelope(response, status_code=422, code="validation_error")
    assert "daily_metrics" in error["message"]
    assert error["details"] is None


async def test_export_rejects_an_inverted_window_exactly_as_the_json_routes_do(
    client, db_session, assert_error_envelope
):
    """An inverted window is refused by the export, with the JSON routes' answer.

    ``resolve_window`` is shared, so both surfaces must answer identically — not
    merely "both fail". A reversed range that reached an aggregation would come
    back as an empty file, which reads exactly like "you did nothing".

    The two envelopes are compared field by field rather than with ``==`` because
    ``request_id`` is unique per request by design: two requests for the same
    invalid window must differ in exactly that field, and a whole-envelope ``==``
    would assert the opposite of what the middleware is there to guarantee.
    """
    _, auth = await seeded_client(client, db_session)
    query = f"start_date={WINDOW_END}&end_date={WINDOW_START}"

    csv_response = await client.get(f"/api/v1/analytics/export.csv?{query}", headers=auth)
    json_response = await client.get(f"/api/v1/analytics/overview?{query}", headers=auth)

    csv_error = assert_error_envelope(csv_response, status_code=422, code="validation_error")
    json_error = assert_error_envelope(json_response, status_code=422, code="validation_error")
    assert csv_error["request_id"] != json_error["request_id"]
    assert {key: csv_error[key] for key in ("code", "message", "details")} == {
        key: json_error[key] for key in ("code", "message", "details")
    }


async def test_export_rejects_an_oversized_window_exactly_as_the_json_routes_do(
    client, db_session, settings
):
    """A window wider than the ceiling is refused, with the same answer as JSON.

    One day past ``ANALYTICS_MAX_RANGE_DAYS`` is the boundary that matters, so the
    widest legal window is asserted to succeed and one day more to fail.
    """
    _, auth = await seeded_client(client, db_session)
    widest_end = WINDOW_START + timedelta(days=settings.analytics_max_range_days - 1)
    over_end = widest_end + timedelta(days=1)

    legal = await client.get(
        f"/api/v1/analytics/export.csv?start_date={WINDOW_START}&end_date={widest_end}",
        headers=auth,
    )
    assert legal.status_code == 200, legal.text

    over_csv = await client.get(
        f"/api/v1/analytics/export.csv?start_date={WINDOW_START}&end_date={over_end}", headers=auth
    )
    over_json = await client.get(
        f"/api/v1/analytics/overview?start_date={WINDOW_START}&end_date={over_end}", headers=auth
    )
    assert over_csv.status_code == over_json.status_code == 422
    assert over_csv.json()["error"]["code"] == "validation_error"


# ---------------------------------------------------------------------------
# The date range is honoured
# ---------------------------------------------------------------------------


async def test_the_window_selects_which_rows_are_exported(client, db_session):
    """Narrowing the window drops the days outside it and keeps the ones inside.

    Three days are rebuilt and each is then exported on its own, so the window is
    proved to *filter* rather than merely to be accepted.
    """
    seed, auth = await seeded_client(client, db_session)
    await seed_window(seed)
    await rebuild(client, auth)

    for day in (DAY, DAY + timedelta(days=1), DAY + timedelta(days=2)):
        response = await client.get(
            f"/api/v1/analytics/export.csv?dataset=daily_metrics&start_date={day}&end_date={day}",
            headers=auth,
        )
        assert response.status_code == 200, response.text
        parsed = rows_of(response.text)
        assert len(parsed) == 2, day
        assert parsed[1][0] == day.isoformat()
        assert response.headers["Content-Disposition"] == (
            f'attachment; filename="nexus-daily_metrics-{day.isoformat()}-{day.isoformat()}.csv"'
        )


async def test_the_window_bounds_the_task_and_session_exports_too(client, db_session):
    """``task_performance`` and ``work_sessions`` filter by the same window.

    Both filter on a half-open UTC instant range rather than on ``daily_metrics``'
    ``metric_date``, so the boundary has to be checked directly: a task created at
    00:00 on the day *after* the window must be absent, not merely untallied.
    """
    seed, auth = await seeded_client(client, db_session)
    await seed_window(seed)

    day_one = await client.get(
        f"/api/v1/analytics/export.csv?dataset=task_performance&start_date={DAY}&end_date={DAY}",
        headers=auth,
    )
    parsed = rows_of(day_one.text)
    header, *data = parsed
    # Day 1 created five tasks; day 2 and day 3 fall outside the window.
    assert len(data) == 5
    assert all(
        instant(row[header.index("created_at")]).astimezone(UTC).date() == DAY for row in data
    )

    sessions = await client.get(
        f"/api/v1/analytics/export.csv?dataset=work_sessions&start_date={DAY}&end_date={DAY}",
        headers=auth,
    )
    assert len(rows_of(sessions.text)) - 1 == 2


# ---------------------------------------------------------------------------
# Owner scoping
# ---------------------------------------------------------------------------


async def test_a_second_account_exports_only_its_own_rows(client, db_session):
    """Two accounts, two files, no overlap in either direction.

    The export is the surface that leaves the database, so this is asserted on the
    file the caller would keep: Ada's project name appears in no row of Grace's
    file, Ada's task ids appear nowhere in it, and Grace's row count is her own.
    """
    ada, ada_auth = await seeded_client(client, db_session, username="ada", email="ada@nexus.test")
    await seed_window(ada)
    await rebuild(client, ada_auth)
    grace, grace_auth = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    grace_project = await grace.project()
    await grace.task(project_id=grace_project.id, title="grace only")

    ada_export = await export(client, ada_auth, "task_performance")
    grace_export = await export(client, grace_auth, "task_performance")

    header = rows_of(ada_export.text)[0]
    ada_rows = rows_of(ada_export.text)[1:]
    grace_rows = rows_of(grace_export.text)[1:]
    assert len(ada_rows) == 7
    assert len(grace_rows) == 1

    ada_ids = {row[header.index("task_id")] for row in ada_rows}
    grace_ids = {row[header.index("task_id")] for row in grace_rows}
    assert ada_ids.isdisjoint(grace_ids)
    assert grace_rows[0][header.index("title")] == "grace only"
    # Grace's row names Grace's project and no other: the ``projects`` side of
    # the export's outer join is reached through ``tasks.owner_id``, so a leaked
    # join would show up here as one of Ada's names appearing in Grace's file.
    assert {row[header.index("project_name")] for row in grace_rows} == {grace_project.name}
    assert {row[header.index("project_name")] for row in ada_rows}.isdisjoint({grace_project.name})


async def test_a_second_account_does_not_inherit_the_first_account_aggregates(client, db_session):
    """Grace's ``daily_metrics`` are her own rows, not Ada's rebuilt ones.

    The rebuild is owner-scoped, so Grace's file is all zeros even though the same
    table is full of Ada's activity — the strongest statement of tenancy this
    surface can make.
    """
    ada, ada_auth = await seeded_client(client, db_session, username="ada", email="ada@nexus.test")
    await seed_window(ada)
    await rebuild(client, ada_auth)
    _, grace_auth = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )
    await rebuild(client, grace_auth)

    grace_export = await export(client, grace_auth, "daily_metrics")

    parsed = rows_of(grace_export.text)
    assert len(parsed) == 4
    for row in parsed[1:]:
        assert row[1:13] == ["0"] * 12, row


async def test_a_project_id_the_caller_does_not_own_changes_nothing(client, db_session):
    """An unowned ``project_id`` cannot narrow an export to somebody else's data.

    The export route takes no ``project_id`` at all, and an unrecognised query
    parameter is ignored rather than honoured. That is the safe outcome, and it is
    asserted as one: the response is byte-identical to the same request without
    the parameter, and it contains no row from the other account's project.
    """
    ada, _ = await seeded_client(client, db_session, username="ada", email="ada@nexus.test")
    ids = await seed_window(ada)
    _, grace_auth = await seeded_client(
        client, db_session, username="grace", email="grace@nexus.test"
    )

    plain = await export(client, grace_auth, "task_performance")
    scoped = await export(
        client, grace_auth, "task_performance", extra=f"project_id={ids['alpha'].id}"
    )

    assert scoped.status_code == 200
    assert scoped.text == plain.text
    assert rows_of(scoped.text) == rows_of(plain.text)


async def test_export_requires_authentication(client, db_session):
    """No token, no file — and the refusal is the shared envelope, not a 500."""
    seed, _ = await seeded_client(client, db_session)
    await seed_window(seed)

    response = await client.get("/api/v1/analytics/export.csv?dataset=daily_metrics")

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "unauthorized"
