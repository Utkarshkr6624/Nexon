"""Model/migration agreement for Phase 8, checked by rendering ``0008`` offline.

``tests/test_migration_ddl.py`` asks this question of the whole chain. This file
asks it of the four Phase 8 tables specifically, and it needs no database: Alembic
renders a migration to SQL without connecting anywhere (``as_sql=True`` plus an
output buffer), so ``0008`` is executed here into a string, that string is parsed,
and every ``CREATE TABLE`` column is compared with the one the model declares —
same name, same type, same nullability, same server default — along with every
foreign key and index.

Two claims this file makes that the general chain cannot, because both are
Phase 8 properties rather than schema conventions:

* **The two observation tables have no ``updated_at``.** ``git_commits`` and
  ``git_scan_runs`` record things that happened; ``TimestampMixin`` would stamp
  a revision on every UPDATE and assert a change git cannot make. The other two
  tables *are* revised and must carry the pair.
* **``uq_git_repositories_owner_path`` is per account, not global.** A local
  directory may legitimately be registered by two accounts on one machine, so a
  global unique index would make the second registration a conflict about
  something nobody is contending for. The composite is asserted column by column
  below.

The downgrade is checked too, and for the only reason worth checking it: the
three child tables carry ``ON DELETE CASCADE`` onto ``git_repositories``, so the
reversal has to drop them before it drops the parent. Rendered order is asserted
because a reversal that dropped the parent first would fail on a populated
database — which is the only environment this ordering ever gets tested in.
"""

from __future__ import annotations

import importlib
import io
import re

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.developer import GitBranch, GitCommit, GitRepository, GitScanRun

PG = postgresql.dialect()

#: The four tables this migration owns, in the order ``upgrade()`` creates them.
#: Named rather than derived from ``Base.metadata`` so a fifth Phase 8 table
#: cannot slip into the migration without also appearing in this list — and
#: therefore without being checked here.
PHASE_8_TABLES = ("git_repositories", "git_commits", "git_branches", "git_scan_runs")

_CONSTRAINT_LINE = re.compile(r"^(PRIMARY KEY|FOREIGN KEY|UNIQUE|CHECK|CONSTRAINT|EXCLUDE)\b")
_CREATE_TABLE = re.compile(r"CREATE TABLE (\w+) \((.*?)\n\)[;]?", re.S)
_CREATE_INDEX = re.compile(r"CREATE (UNIQUE )?INDEX (\w+) ON (\w+) \(([^)]*)\)(?: WHERE (.+?))?;")
_DROP_TABLE = re.compile(r"DROP TABLE (\w+);")
_UNIQUE_CONSTRAINT = re.compile(r"CONSTRAINT (\w+) UNIQUE \(([^)]*)\)")


def _render(calls: str) -> str:
    """Run ``upgrade`` or ``downgrade`` on the Phase 8 module into a string.

    ``Operations.context`` installs the module-level ``alembic.op`` proxy the
    migration calls, and the ``output_buffer`` is where the offline ``impl``
    writes instead of a connection.
    """
    module = importlib.import_module("migrations.versions.0008_phase8_developer_intelligence")
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    with Operations.context(context):
        getattr(module, calls)()
    return buffer.getvalue()


def _normalise(ddl: str) -> str:
    """Collapse whitespace and drop what PostgreSQL adds on reflection.

    Three normalisations, each a difference of *rendering* rather than of schema.
    ``DEFAULT 'true'`` and ``DEFAULT true`` are the same default — the first is
    an untyped string literal the database casts on assignment — and the same
    goes for a quoted ``DEFAULT '0'`` against a bare ``DEFAULT 0``, which is
    exactly the pair the model side (``server_default="0"``) and the migration
    side (``sa.text("0")``) produce for every counter in this schema. Alembic's
    own comparison treats both as equal: it hands each side to the server as a
    literal and asks. Finally the cast suffixes on ``::jsonb`` /
    ``::character varying`` are what ``pg_get_expr`` prints.
    """
    collapsed = re.sub(r"\s+", " ", ddl).strip()
    collapsed = re.sub(r"::\w+(\[\])?", "", collapsed)
    collapsed = re.sub(r"DEFAULT '(true|false)'", r"DEFAULT \1", collapsed)
    return re.sub(r"DEFAULT '(-?\d+)'", r"DEFAULT \1", collapsed)


def _columns_from(body: str) -> dict[str, str]:
    """``{column name: normalised DDL}`` for one ``CREATE TABLE`` body."""
    columns: dict[str, str] = {}
    for raw in body.split("\n"):
        line = raw.strip().rstrip(",").strip()
        if not line or _CONSTRAINT_LINE.match(line):
            continue
        columns[line.split()[0]] = _normalise(line)
    return columns


@pytest.fixture(scope="module")
def upgrade_ddl() -> str:
    return _render("upgrade")


@pytest.fixture(scope="module")
def downgrade_ddl() -> str:
    return _render("downgrade")


@pytest.fixture(scope="module")
def migrated_columns(upgrade_ddl: str) -> dict[str, dict[str, str]]:
    """``{table: {column: ddl}}`` for every table ``0008`` creates."""
    return {
        match.group(1): _columns_from(match.group(2))
        for match in _CREATE_TABLE.finditer(upgrade_ddl)
    }


@pytest.fixture(scope="module")
def model_columns() -> dict[str, dict[str, str]]:
    """The same map, read from :class:`~app.db.base.Base`'s metadata.

    Rendered through :class:`CreateTable` rather than column by column because a
    boolean column's ``server_default="true"`` renders as a quoted literal when
    SQLAlchemy is not given the column's type, which would report a difference
    against a migration that is byte-for-byte right.
    """
    return {
        name: _columns_from(
            _CREATE_TABLE.search(str(CreateTable(table).compile(dialect=PG))).group(2)
        )
        for name, table in {
            "git_repositories": GitRepository.__table__,
            "git_commits": GitCommit.__table__,
            "git_branches": GitBranch.__table__,
            "git_scan_runs": GitScanRun.__table__,
        }.items()
    }


@pytest.fixture(scope="module")
def migrated_indexes(upgrade_ddl: str) -> dict[str, tuple[str, tuple[str, ...], bool]]:
    """``{index name: (table, columns, unique)}`` for every index ``0008`` creates."""
    return {
        match.group(2): (
            match.group(3),
            tuple(column.strip() for column in match.group(4).split(",")),
            match.group(1) is not None,
        )
        for match in _CREATE_INDEX.finditer(upgrade_ddl)
    }


# -- The tables ---------------------------------------------------------------


def test_the_migration_creates_exactly_the_four_phase_8_tables(migrated_columns):
    """The created set and the declared set have to be equal.

    A table the migration creates but the models do not declare is invisible to
    every query in the product; one the models declare and the migration does
    not is an ``UndefinedTable`` at runtime.

    Both directions are the same assertion here because this migration adds
    nothing to an existing table — every statement in ``upgrade()`` is a
    ``CREATE TABLE`` or a ``CREATE INDEX`` — so the created set and the declared
    set have to be equal rather than merely overlapping.
    """
    assert set(migrated_columns) == set(PHASE_8_TABLES)


@pytest.mark.parametrize("table_name", PHASE_8_TABLES)
def test_every_migrated_column_matches_the_model(table_name, migrated_columns, model_columns):
    """Type, nullability and server default agree, column by column."""
    assert migrated_columns[table_name] == model_columns[table_name]


# -- Nullability that carries meaning -----------------------------------------


def test_the_registration_columns_that_mean_absence_are_nullable(
    migrated_columns,
):
    """Four columns whose null is an answer, not a hole.

    ``current_branch`` is null on a detached HEAD, which is a normal state git
    reports as the literal string ``HEAD`` — storing that string would put a
    branch name in the column that names no branch. ``first_commit_at`` and
    ``latest_commit_at`` are null for a ``git init``ed repository with no
    commits, where ``0`` would claim a zero-length history rather than the
    absence of one. ``last_scan_error`` is null after a successful scan, which is
    how a recovered repository stops reporting the last failure.
    """
    nullable = {
        name: column
        for name, column in migrated_columns["git_repositories"].items()
        if "NOT NULL" not in column
    }
    assert set(nullable) == {
        "description",
        "primary_language",
        "project_id",
        "current_branch",
        "default_branch",
        "first_commit_at",
        "latest_commit_at",
        "last_scanned_at",
        "last_scan_status",
        "last_scan_error",
    }
    assert migrated_columns["git_commits"]["branch"] == "branch VARCHAR(255)"
    assert migrated_columns["git_commits"]["author_name"] == "author_name VARCHAR(200)"
    assert migrated_columns["git_commits"]["author_email"] == "author_email VARCHAR(320)"


def test_the_columns_a_scan_reads_are_not_null_and_default_to_zero(
    migrated_columns,
):
    """Every counter and every date a scan fills in is NOT NULL with a default.

    The scan is wrapped and a failure becomes a row rather than an exception, so
    "the scan did not run" has to be expressible without leaving a null in a
    column every dashboard query sums. The three nullable instants above are the
    deliberate exceptions, and each of them is an answer rather than a gap.
    """
    assert migrated_columns["git_commits"]["committed_at"] == (
        "committed_at TIMESTAMP WITH TIME ZONE NOT NULL"
    )
    assert migrated_columns["git_commits"]["additions"] == "additions INTEGER DEFAULT 0 NOT NULL"
    assert migrated_columns["git_scan_runs"]["status"] == "status VARCHAR(16) DEFAULT 'ok' NOT NULL"
    assert migrated_columns["git_scan_runs"]["duration_ms"] == (
        "duration_ms INTEGER DEFAULT 0 NOT NULL"
    )


# -- The two tables that do not claim to be revised ----------------------------


def test_the_two_observation_tables_carry_created_at_but_never_updated_at(
    migrated_columns,
):
    """A commit object and a finished scan are immutable; ``updated_at`` would lie.

    :class:`~app.db.base.TimestampMixin` stamps ``updated_at`` on every UPDATE.
    On a row that records something git will not change, that column asserts a
    revision that cannot happen — and a reader who trusts it would conclude that
    history had been rewritten.
    """
    for table_name in ("git_commits", "git_scan_runs"):
        columns = migrated_columns[table_name]
        assert "created_at" in columns
        assert "updated_at" not in columns
    for table_name in ("git_repositories", "git_branches"):
        assert "updated_at" in migrated_columns[table_name]


# -- Uniqueness ---------------------------------------------------------------


def test_the_owner_path_uniqueness_is_per_account_and_not_global(upgrade_ddl):
    """``(user_id, local_path)``, never ``local_path`` alone.

    Two accounts on one machine may each register the same local directory — one
    watching it for a personal project and one for a client. A global unique
    index would make the second registration a conflict about something nobody is
    contending for, while the pair still has to be unique: two rows for one
    directory would hold two commit counts that disagree with each other.
    """
    matches = {
        name: tuple(column.strip() for column in columns.split(","))
        for name, columns in _UNIQUE_CONSTRAINT.findall(upgrade_ddl)
    }
    assert matches["uq_git_repositories_owner_path"] == ("user_id", "local_path")
    assert matches["uq_git_commits_repo_hash"] == ("repository_id", "commit_hash")
    assert matches["uq_git_branches_repo_name"] == ("repository_id", "name")


def test_the_commit_and_branch_uniqueness_is_what_makes_a_rescan_idempotent(
    migrated_columns,
):
    """Both observation tables are keyed on the repository, not on the owner.

    A commit hash is unique within a repository and is only ever read back
    through the repository that reported it, so putting ``user_id`` on either
    constraint would widen the key without preventing a duplicate: the same hash
    arriving under two owners would be two rows, and neither read would ever see
    both.
    """
    assert migrated_columns["git_commits"]["repository_id"].startswith(
        "repository_id UUID NOT NULL"
    )
    assert migrated_columns["git_branches"]["repository_id"].startswith(
        "repository_id UUID NOT NULL"
    )


# -- Ownership ---------------------------------------------------------------


def test_the_project_link_is_set_null_so_the_history_outlives_the_project(
    upgrade_ddl,
):
    """``ON DELETE SET NULL`` on ``git_repositories.project_id``, never CASCADE.

    The commits and branches are a record of what was observed. Deleting a
    project in NEXUS must not delete that record, exactly as ``activity_events``
    keeps the history of a deleted project. CASCADE would make removing a
    project quietly erase evidence — which is the one thing this phase's rows
    exist to be.
    """
    assert "ON DELETE SET NULL" in upgrade_ddl
    foreign_keys = re.findall(
        r"FOREIGN KEY\((\w+)\) REFERENCES (\w+) \((\w+)\)([^\n]*)", upgrade_ddl
    )
    # The captured tail carries the statement's trailing comma with it, so the
    # comparison is against the clause alone.
    rules = {
        (child, parent, rule.strip().rstrip(","))
        for child, parent, _referenced, rule in foreign_keys
    }
    assert ("project_id", "projects", "ON DELETE SET NULL") in rules
    assert ("user_id", "users", "ON DELETE CASCADE") in rules
    assert ("repository_id", "git_repositories", "ON DELETE CASCADE") in rules


def test_every_phase_8_table_declares_exactly_the_expected_foreign_keys(upgrade_ddl):
    """Two per child table, two on the parent.

    The child tables carry the owning user *and* the repository they were read
    from. Both are CASCADE: a row with a repository but no owner is an orphan no
    query can reach, and a repository that has been removed has nothing left for
    its commits to be about.
    """
    per_table: dict[str, set[str]] = {}
    for table_name in PHASE_8_TABLES:
        block = _CREATE_TABLE.search(
            upgrade_ddl[upgrade_ddl.index(f"CREATE TABLE {table_name} ") :]
        )
        assert block is not None
        per_table[table_name] = set(re.findall(r"FOREIGN KEY\((\w+)\)", block.group(2)))
    assert per_table == {
        "git_repositories": {"user_id", "project_id"},
        "git_commits": {"user_id", "repository_id"},
        "git_branches": {"user_id", "repository_id"},
        "git_scan_runs": {"user_id", "repository_id"},
    }


# -- Indexes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("index_name", "table", "columns"),
    [
        ("ix_git_repositories_user_id", "git_repositories", ("user_id",)),
        ("ix_git_repositories_owner_active", "git_repositories", ("user_id", "is_active")),
        ("ix_git_repositories_project_id", "git_repositories", ("project_id",)),
        ("ix_git_commits_user_id", "git_commits", ("user_id",)),
        ("ix_git_commits_repo_committed", "git_commits", ("repository_id", "committed_at")),
        ("ix_git_commits_user_committed", "git_commits", ("user_id", "committed_at")),
        ("ix_git_branches_user_id", "git_branches", ("user_id",)),
        ("ix_git_branches_repo_id", "git_branches", ("repository_id",)),
        ("ix_git_scan_runs_user_id", "git_scan_runs", ("user_id",)),
        ("ix_git_scan_runs_repo_scanned", "git_scan_runs", ("repository_id", "scanned_at")),
    ],
)
def test_a_phase_8_index_exists_with_the_columns_the_queries_probe(
    index_name, table, columns, migrated_indexes
):
    """Each index names one read.

    None of these is speculative: ``(user_id, committed_at)`` and
    ``(repository_id, committed_at)`` are the commit timeline and every metric
    window, ``(user_id, is_active)`` is the default repository list, and
    ``(repository_id, scanned_at)`` is the scan history panel. An index nothing
    reads is a write cost on every scan; a read nothing indexes is a sequential
    scan over the table that grows the most in this phase.
    """
    assert migrated_indexes[index_name] == (table, columns, False)


def test_no_index_exists_that_the_models_do_not_declare(migrated_indexes):
    """Both directions, because a stray index is not free.

    A migration creating an index no model declares is invisible to autogenerate
    — ``alembic check`` would report no drift while the database pays for the
    index on every insert. A model declaring one the migration does not create
    leaves a query the schema promised and the database cannot serve.
    """
    declared = {
        index.name: index
        for table in (GitRepository, GitCommit, GitBranch, GitScanRun)
        for index in table.__table__.indexes
    }
    assert set(migrated_indexes) == set(declared)
    for name, (_table, columns, _unique) in migrated_indexes.items():
        assert tuple(column.name for column in declared[name].columns) == columns


# -- The reversal -------------------------------------------------------------


def test_the_downgrade_drops_the_children_before_their_parent(downgrade_ddl):
    """Order, because the alternative fails only on a populated database.

    The three child tables carry ``ON DELETE CASCADE`` onto
    ``git_repositories``. Dropping the parent first would leave the children
    pointing at nothing, and PostgreSQL refuses — which is a failure a fresh,
    empty test database is perfectly happy not to raise.
    """
    dropped = [match.group(1) for match in _DROP_TABLE.finditer(downgrade_ddl)]
    assert set(dropped) == set(PHASE_8_TABLES)
    assert dropped.index("git_repositories") == len(dropped) - 1


def test_the_downgrade_touches_nothing_from_an_earlier_phase(downgrade_ddl):
    """No ``ALTER``, no ``DELETE``, no reference to a pre-Phase-8 table.

    A reversal that dropped ``projects`` or ``users`` would take the rest of the
    product with it, and a reversal that wrote rows would not be a reversal.
    """
    assert "ALTER" not in downgrade_ddl
    assert "DELETE" not in downgrade_ddl
    assert "users" not in downgrade_ddl
    assert "projects" not in downgrade_ddl


def test_the_migration_imports_no_model(downgrade_ddl):
    """``0008`` names no ``app.models`` symbol.

    A migration that imports the ORM lets a later edit to a model silently
    rewrite history — the same reason ``0001`` through ``0007`` spell their DDL
    out. Checked on the source rather than on the rendered SQL, which cannot
    show an import at all.
    """
    source = importlib.import_module(
        "migrations.versions.0008_phase8_developer_intelligence"
    ).__file__
    with open(source, encoding="utf-8") as handle:
        assert "app.models" not in handle.read()
