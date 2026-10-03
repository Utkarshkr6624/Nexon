"""Phase 9's learning domain, asserted at both ends of its surface.

What this file covers
---------------------
Phase 9 puts a rule where it can be broken quietly in two different places, so
this file asserts both of them.

**The declared schema and migration ``0009``** — every column the three learning
tables declare, its type, its nullability and its server default; every
``UniqueConstraint``, ``Index`` and ``CheckConstraint`` *by name*; the
``ondelete`` rule on every foreign key; and the four properties that are Phase 9
rules rather than schema conventions:

* **A level is 1-5 and the check constraints hold it there** — not merely the
  Pydantic schema, because a background job writing a 7 must not be able to.
* **``learning_activities`` carries no ``updated_at``.** It is append-only; an
  ``onupdate`` stamp would assert that a recorded moment is still being revised.
  ``learning_goals`` and ``skills`` *are* revised and must carry the pair.
* **No enum column is a SQLAlchemy ``Enum``** — every vocabulary is
  ``Mapped[str]`` in a sized ``String``, validated in Python. This is the house
  rule from :mod:`app.models.enums`, and it is asserted per column rather than
  assumed.
* **There are no ``relationship()`` attributes** on any of the three mappers.
  Navigation is an explicit owner-scoped ``select()``; a relationship invites
  exactly the lazy load that forgets ``user_id``, and the phase says another
  account's goal is a 404 rather than a 403.

The first half reads ``Base.metadata``, because what matters is that the
*declared* schema encodes the phase's rules — a migration faithful to a wrong
model would have to fail here first. It then renders migration ``0009`` offline
(``as_sql=True`` plus an output buffer: Alembic executes a migration into a
string without connecting anywhere) and compares the three learning tables
column by column, so the two cannot drift. ``0009`` also creates the three career
tables, which belong to another module and are out of scope here;
``tests/test_migration_ddl.py`` covers the whole of the revision.

**The wire schemas** in :mod:`app.schemas.learning` — two promises that are easy
to state and easy to break by accident, and neither leaves a trace when it
breaks, because the response is still valid JSON and the failure shows up as a
sentence on a screen rather than as an exception:

* **Every nullable field is required and typed ``T | None``.** The key is always
  present. A client that finds ``last_activity_at`` missing cannot tell a skill
  nobody has touched from a payload written by a different version of the
  server, and those want different sentences. ``Optional[str] = None`` is the
  shape that breaks this, so it is banned by source inspection as well as by the
  field check — it is banned *because* a field-level check cannot always see it:
  Pydantic rewrites ``Optional[X]`` and ``X | None`` into the same thing
  internally, so only reading the module catches a rewrite that quietly
  reintroduced the default.
* **A figure that could not be computed is ``None``, never ``0``.** Spelled out in
  the tests below for the three places it bites: a duration nobody recorded, a
  gap nobody measured, and a metric over an empty denominator.

The halves are kept in one file rather than two because they assert the same
rules from the two ends: a reader asking "is a null allowed here?" needs the
column's nullability and the field's nullability in front of them at once, and
neither list is intelligible without the other.

Neither half needs a database. A rule that could only be checked against a live
server is a rule nobody re-checks, so this file can be run while another agent
holds the test PostgreSQL and never competes for the ``nexus_test`` lock.
"""

from __future__ import annotations

import ast
import importlib
import io
import re
import types
import typing
import uuid
from datetime import UTC, date, datetime

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import BaseModel, ValidationError
from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import RelationshipProperty
from sqlalchemy.schema import CreateTable

from app.db.base import Base
from app.models.enums import (
    LearningActivityType,
    LearningGoalStatus,
    ProjectPriority,
    SkillLevelSource,
)
from app.models.learning import (
    DEFAULT_LEARNING_GOAL_PRIORITY,
    DEFAULT_LEARNING_GOAL_STATUS,
    DEFAULT_SKILL_CURRENT_LEVEL,
    DEFAULT_SKILL_LEVEL_SOURCE,
    DEFAULT_SKILL_TARGET_LEVEL,
    MAX_SKILL_LEVEL,
    MIN_SKILL_LEVEL,
    LearningActivity,
    LearningGoal,
    Skill,
)
from app.schemas.learning import (
    LEARNING_FEATURE_SCHEMA_VERSION,
    LearningActivityBucketRead,
    LearningActivityListRead,
    LearningActivityRead,
    LearningActivitySeriesRead,
    LearningActivityWrite,
    LearningFeatureValues,
    LearningFeatureVectorRead,
    LearningGoalListRead,
    LearningGoalRead,
    LearningGoalUpdate,
    LearningGoalWrite,
    LearningGoalWriteBase,
    LearningMetricRead,
    LearningSummaryRead,
    SkillGapListRead,
    SkillGapRead,
    SkillListRead,
    SkillRead,
    SkillUpdate,
    SkillWrite,
)

#: The three tables this module declares. Named rather than filtered out of
#: ``Base.metadata`` so a fourth Phase 9 table has to be argued for here — the
#: most likely candidate being a stored skill gap, which the module docstring
#: deliberately does not build.
LEARNING_TABLES = ("learning_goals", "skills", "learning_activities")

_MODELS = (LearningGoal, Skill, LearningActivity)


# -- Shared helpers ------------------------------------------------------------


def _table(model: type) -> object:
    """The ``Table`` a declarative model owns."""
    return model.__table__


def _columns(model: type) -> dict[str, object]:
    """``{column name: Column}`` for one model, primary key included."""
    return {column.name: column for column in _table(model).columns}


def _server_default(column: object) -> str | None:
    """The rendered text of a column's ``server_default``, or ``None``.

    A default written as a string (``server_default="0"``) and one written as an
    expression (``server_default=func.now()``) are both rendered here, so the
    assertion can be written against the text the database would actually see.
    """
    default = column.server_default
    return None if default is None else str(default.arg)


def _whitespace(text: str) -> str:
    """Collapse runs of whitespace so a reformatted constraint still compares equal."""
    return re.sub(r"\s+", " ", text).strip()


def _checks(model: type) -> dict[str, str]:
    """``{constraint name: normalised SQL}`` for a model's check constraints."""
    return {
        constraint.name: _whitespace(str(constraint.sqltext))
        for constraint in _table(model).constraints
        if isinstance(constraint, CheckConstraint)
    }


def _indexes(model: type) -> dict[str, tuple[tuple[str, ...], bool]]:
    """``{index name: (columns, unique)}`` for a model's indexes."""
    return {
        index.name: (tuple(column.name for column in index.columns), index.unique)
        for index in _table(model).indexes
        if isinstance(index, Index)
    }


def _uniques(model: type) -> dict[str, tuple[str, ...]]:
    """``{constraint name: columns}`` for a model's unique constraints."""
    return {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in _table(model).constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_keys(model: type) -> dict[str, tuple[str, str | None]]:
    """``{column name: (target table, ondelete)}`` for every foreign-key column."""
    resolved: dict[str, tuple[str, str | None]] = {}
    for name, column in _columns(model).items():
        for foreign_key in column.foreign_keys:
            resolved[name] = (foreign_key.column.table.name, foreign_key.ondelete)
    return resolved


def _is_optional(annotation: object) -> bool:
    """Whether ``annotation`` admits ``None``.

    Pydantic has already resolved the annotation into real types by the time the
    field is constructed, so this reads the union's members rather than parsing
    the source.
    """
    return typing.get_origin(annotation) in (typing.Union, types.UnionType) and type(
        None
    ) in typing.get_args(annotation)


def _example(annotation: object) -> object:
    """A minimal valid value for ``annotation``, or ``None`` if it admits it.

    Used to build a complete instance of a read shape without hand-writing one per
    model: the tests are about *which* keys are present, not about the values, and
    a fixture that has to be updated whenever a column is added is a fixture that
    silently stops testing the models it was written for.
    """
    if annotation is None or annotation is type(None):
        return None
    if _is_optional(annotation):
        members = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        return _example(members[0]) if members else None
    if annotation is uuid.UUID:
        return uuid.UUID(int=1)
    if annotation is bool:
        return True
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is str:
        return "a"
    if annotation is datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)
    if annotation is date:
        return date(2026, 1, 1)
    origin = typing.get_origin(annotation)
    if origin is list:
        return []
    if origin in (dict, typing.Mapping):
        return {}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _minimal(annotation)
    raise AssertionError(f"No example value defined for {annotation!r}.")


def _minimal(model: type[BaseModel]) -> BaseModel:
    """A complete instance of ``model``, every nullable left null."""
    payload: dict[str, object] = {
        name: _example(field.annotation) for name, field in model.model_fields.items()
    }
    payload.update(MINIMAL_OVERRIDES.get(model, {}))
    return model(**payload)


def _payload(model: type[BaseModel], **overrides: object) -> dict[str, object]:
    """A full keyword payload for ``model``, with ``overrides`` applied."""
    payload: dict[str, object] = {
        name: _example(field.annotation) for name, field in model.model_fields.items()
    }
    payload.update(overrides)
    return payload


def _annotations_in_source(path: str) -> list[str]:
    """Every annotation the module writes, as source text.

    Parsed rather than grepped, because a grep cannot tell an annotation from a
    docstring — and this module's docstrings name ``Optional[T] = None`` several
    times precisely to explain why it is banned. Only the annotations are returned.
    """
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    found: list[str] = []
    for node in ast.walk(tree):
        annotation = getattr(node, "annotation", None)
        if annotation is not None and isinstance(node, (ast.AnnAssign, ast.arg)):
            found.append(ast.unparse(annotation))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            found.append(ast.unparse(node.returns))
    return found


def _module_path() -> str:
    """The on-disk path of :mod:`app.schemas.learning`."""
    import app.schemas.learning as module

    assert module.__file__ is not None
    return module.__file__


#: The response shapes the two wire rules apply to. The write models are
#: deliberately **not** in this list and the exemption is argued in
#: :func:`test_a_patch_body_may_be_empty_where_a_response_may_not` rather than
#: left implicit: a PATCH has to be able to omit a key, and "omitted" means
#: "leave this column alone", which is a different statement from "this is null".
#: There is no such reading on a response.
READ_SHAPES: tuple[type[BaseModel], ...] = (
    LearningActivityBucketRead,
    LearningActivityListRead,
    LearningActivityRead,
    LearningActivitySeriesRead,
    LearningFeatureValues,
    LearningFeatureVectorRead,
    LearningGoalListRead,
    LearningGoalRead,
    LearningMetricRead,
    LearningSummaryRead,
    SkillGapListRead,
    SkillGapRead,
    SkillListRead,
    SkillRead,
)

#: The eight column names ``learning_features.v1`` freezes. Spelled out here
#: rather than imported, because the point of the test is that the wire still
#: says what the contract said after somebody tidied the module.
FROZEN_FEATURE_NAMES = (
    "sessions_last_7d",
    "sessions_last_30d",
    "learning_minutes",
    "goal_progress",
    "goal_deadline_distance_days",
    "completion_rate",
    "learning_consistency",
    "skill_activity_frequency",
)

#: Exactly which fields on each read shape admit null, and why. A list shape has
#: none — every one of its members is a count, and a count that "could not be
#: computed" is a missing query rather than a missing cell. Everything else is
#: either a pointer that no longer resolves, a duration nobody recorded, or a
#: figure over an empty denominator.
#:
#: These mirror the nullable column sets asserted in
#: :func:`test_only_the_columns_whose_null_is_an_answer_are_nullable` one for
#: one; they are written out again rather than derived, because a wire field that
#: quietly became required — or a column that quietly became ``NOT NULL`` — is
#: exactly the drift the pair of assertions exists to catch, and a derived
#: expectation could not fail.
EXPECTED_NULLABLE: dict[type[BaseModel], set[str]] = {
    LearningActivityBucketRead: {"minutes"},
    LearningActivityListRead: set(),
    LearningActivityRead: {
        "skill_id",
        "goal_id",
        "description",
        "duration_minutes",
        "source_type",
        "source_id",
    },
    LearningActivitySeriesRead: {"skill_id", "total_minutes"},
    LearningFeatureValues: {
        "learning_minutes",
        "goal_progress",
        "goal_deadline_distance_days",
        "completion_rate",
        "learning_consistency",
        "skill_activity_frequency",
    },
    LearningFeatureVectorRead: set(),
    LearningGoalListRead: set(),
    LearningGoalRead: {
        "description",
        "target_skill_id",
        "target_topic",
        "target_date",
        "estimated_effort_minutes",
        "project_id",
        "note_id",
        "completed_at",
    },
    LearningMetricRead: {"value", "window_days", "reason_if_unavailable"},
    LearningSummaryRead: {"minutes_in_window", "latest_activity_at"},
    SkillGapListRead: set(),
    SkillGapRead: {"skill_id", "days_since_last_activity", "reason_if_unavailable"},
    SkillListRead: set(),
    SkillRead: {"category", "description", "last_activity_at"},
}

#: Values a validator on the model rejects, so a bare generated example cannot
#: build these two. A skill's level needs a recognised provenance and a gap's
#: explanation needs a figure in it — both of which the tests below assert in
#: their own right, so here they are simply supplied.
MINIMAL_OVERRIDES: dict[type[BaseModel], dict[str, object]] = {
    SkillRead: {"level_source": "user_defined"},
    SkillGapRead: {"explanation": "Target 4/5, current self-assessed 2/5."},
}


# ============================================================================ #
# Half one: what the tables are, and what migration 0009 writes                #
# ============================================================================ #

# -- The tables ---------------------------------------------------------------


def test_the_three_declared_tables_are_exactly_the_phase_9_learning_ones():
    """Declared set and contract set are equal, both directions.

    A table nobody declares is invisible to every query; a table a model
    declares that no migration creates is an ``UndefinedTable`` at runtime. The
    career half of Phase 9 lives in its own module and its own set, so neither
    side can quietly grow into this assertion.
    """
    assert LEARNING_TABLES == ("learning_goals", "skills", "learning_activities")
    assert {model.__tablename__ for model in _MODELS} == set(LEARNING_TABLES)


def test_no_stored_skill_gap_table_exists():
    """The gap is computed on read, so nothing in the schema may cache it.

    A ``skill_gaps`` table would be a *second answer* to "how far from my target
    is this skill?" — one that could disagree with the dashboard the moment a
    level was edited, and that could not express the distinction the phase
    requires between a measured ``gap=0`` and an unmeasured skill
    (``available=False`` with a reason). Caching either of those turns the
    distinction into a null.
    """
    names = set(Base.metadata.tables)
    assert not names & {
        "skill_gaps",
        "learning_skill_gaps",
        "learning_progress",
        "learning_estimates",
        "skill_levels_history",
    }


# -- Columns, types and nullability -------------------------------------------


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            LearningGoal,
            {
                "id",
                "user_id",
                "title",
                "description",
                "target_skill_id",
                "target_topic",
                "target_date",
                "priority",
                "status",
                "progress",
                "estimated_effort_minutes",
                "project_id",
                "note_id",
                "completed_at",
                "created_at",
                "updated_at",
            },
        ),
        (
            Skill,
            {
                "id",
                "user_id",
                "name",
                "category",
                "description",
                "current_level",
                "target_level",
                "level_source",
                "confidence",
                "evidence_count",
                "last_activity_at",
                "created_at",
                "updated_at",
            },
        ),
        (
            LearningActivity,
            {
                "id",
                "user_id",
                "skill_id",
                "goal_id",
                "activity_type",
                "title",
                "description",
                "occurred_at",
                "duration_minutes",
                "source_type",
                "source_id",
                "created_at",
            },
        ),
    ],
)
def test_every_column_the_contract_lists_is_present_and_nothing_else(model, expected):
    """The column inventory, exactly.

    Both directions matter. A missing column is a read that cannot be written;
    an extra one is a fact the phase never sanctioned, and the module docstring
    has to argue for every column it carries.
    """
    assert set(_columns(model)) == expected


@pytest.mark.parametrize(
    ("model", "nullable"),
    [
        # A goal can name a topic before a skill exists, and a linked project or
        # note can be deleted without the intention being deleted with it. Every
        # null below is an answer, not a hole.
        (
            LearningGoal,
            {
                "description",
                "target_skill_id",
                "target_topic",
                "target_date",
                "estimated_effort_minutes",
                "project_id",
                "note_id",
                "completed_at",
            },
        ),
        # ``category`` is null because the suggested categories are suggestions;
        # ``last_activity_at`` is null because "nothing recorded yet" and
        # "recorded long ago" are different facts and the staleness rule reads
        # them differently.
        (Skill, {"category", "description", "last_activity_at"}),
        # A study session may name no skill yet, an activity may outlive the goal
        # it was recorded towards, a duration is null for an *event* rather than
        # a *span*, and both source columns are null for a hand-entered row.
        (
            LearningActivity,
            {
                "skill_id",
                "goal_id",
                "description",
                "duration_minutes",
                "source_type",
                "source_id",
            },
        ),
    ],
)
def test_only_the_columns_whose_null_is_an_answer_are_nullable(model, nullable):
    """Nullability is a statement about meaning, so it is asserted per column.

    The dangerous mistake in both directions is visible here: making
    ``duration_minutes`` NOT NULL would force a zero, which claims a measured
    zero-length session rather than the absence of a measurement — the exact
    conflation the phase's ``available=False`` rule exists to prevent.

    :data:`EXPECTED_NULLABLE` pins the same decision on the wire side of the
    boundary; the two sets are written out separately on purpose, so a column that
    became ``NOT NULL`` without its field following fails one assertion and not
    both.
    """
    columns = _columns(model)
    actually_nullable = {name for name, column in columns.items() if column.nullable}
    assert actually_nullable == nullable


@pytest.mark.parametrize(
    ("model", "widths"),
    [
        (LearningGoal, {"title": 200, "target_topic": 200, "priority": 16, "status": 16}),
        (Skill, {"name": 120, "category": 64, "level_source": 24}),
        (LearningActivity, {"title": 200, "activity_type": 32, "source_type": 32}),
    ],
)
def test_the_text_columns_are_sized_from_their_vocabulary_not_rounded_down(model, widths):
    """Every bounded label column is a ``String`` of the agreed width.

    Sizing a column *shorter* than its own vocabulary truncates the single
    longest value on insert, which is why ``models/risk.py`` had to name its
    risk and recommendation widths separately. Truncating
    ``system_estimate`` (15) or ``project_completed`` (17) would fail exactly on
    the rows the phase cares about most.
    """
    columns = _columns(model)
    for name, length in widths.items():
        column_type = columns[name].type
        assert isinstance(column_type, String), f"{model.__tablename__}.{name} is not a String"
        assert column_type.length == length


@pytest.mark.parametrize(
    ("model", "column_name", "vocabulary"),
    [
        (LearningGoal, "status", LearningGoalStatus),
        (LearningGoal, "priority", ProjectPriority),
        (Skill, "level_source", SkillLevelSource),
        (LearningActivity, "activity_type", LearningActivityType),
    ],
)
def test_no_enum_column_is_a_native_or_sqlalchemy_enum(model, column_name, vocabulary):
    """Enums are ``String`` columns validated in Python — never ``Enum``.

    Asserted with the vocabulary attached so the check also proves the column is
    *narrow enough for every member of the enum it claims to store*: a
    ``String(16)`` holding ``system_estimate`` is a truncating column.
    """
    column_type = _columns(model)[column_name].type
    assert not isinstance(column_type, SAEnum)
    assert isinstance(column_type, String)
    longest = max(len(member.value) for member in vocabulary)
    assert longest <= column_type.length
    assert longest > 0


def test_the_two_longest_vocabulary_members_are_the_ones_that_set_the_widths():
    """The two widths that were easy to get wrong, pinned to their cause.

    ``not_started`` (11) sizes the goal status, ``system_estimate`` (15) is why
    the level-source column is 24 rather than 16, and ``project_completed``
    (17) is why the activity type is 32. Named here because a future member
    longer than any of the three is the thing that silently truncates.
    """
    assert max(len(member.value) for member in LearningGoalStatus) == len("not_started")
    assert max(len(member.value) for member in SkillLevelSource) == len("system_estimate")
    assert max(len(member.value) for member in LearningActivityType) == len("project_completed")


def test_every_uuid_column_is_the_postgres_uuid_type():
    """Ids and foreign keys are ``UUID``, never a 36-character string.

    A string id would also defeat ``ON DELETE SET NULL`` semantics for the
    polymorphic ``source_id`` and would make the account-scope probes string
    comparisons instead of index lookups.
    """
    for model in _MODELS:
        for name in ("id", "user_id"):
            assert isinstance(_columns(model)[name].type, PGUUID), f"{model.__tablename__}.{name}"
    for model, names in (
        (LearningGoal, ("target_skill_id", "project_id", "note_id")),
        (LearningActivity, ("skill_id", "goal_id", "source_id")),
    ):
        for name in names:
            assert isinstance(_columns(model)[name].type, PGUUID), f"{model.__tablename__}.{name}"


# -- Defaults -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "column_name", "expected"),
    [
        (LearningGoal, "priority", "medium"),
        (LearningGoal, "status", "not_started"),
        (LearningGoal, "progress", "0"),
        (Skill, "current_level", "1"),
        (Skill, "target_level", "3"),
        (Skill, "level_source", "user_defined"),
        (Skill, "confidence", "0"),
        (Skill, "evidence_count", "0"),
    ],
)
def test_a_column_with_a_sensible_starting_point_declares_it_in_the_database(
    model, column_name, expected
):
    """The default is a database default, not a Python-side habit.

    Two reasons it has to be. A row inserted by a migration, a psql session or a
    future job must not be missing a value the schema says is mandatory; and
    ``current_level=1`` / ``level_source='user_defined'`` is the phase's honesty
    rule expressed as a column default — a skill nobody has estimated is the
    user's claim at the floor, never a silent inference.
    """
    assert _server_default(_columns(model)[column_name]) == expected


def test_the_module_constants_are_the_defaults_the_columns_use():
    """The constants other agents import are the ones written to the columns.

    The service layer and the repositories both read these rather than
    re-typing the literals, so a change to the default cannot leave the Python
    default and the ``server_default`` disagreeing — which is how a row starts
    life with ``medium`` in memory and ``high`` in the database.
    """
    assert DEFAULT_LEARNING_GOAL_PRIORITY == ProjectPriority.MEDIUM.value == "medium"
    assert DEFAULT_LEARNING_GOAL_STATUS == LearningGoalStatus.NOT_STARTED.value == "not_started"
    assert DEFAULT_SKILL_LEVEL_SOURCE == SkillLevelSource.USER_DEFINED.value == "user_defined"
    assert (DEFAULT_SKILL_CURRENT_LEVEL, DEFAULT_SKILL_TARGET_LEVEL) == (1, 3)
    assert (MIN_SKILL_LEVEL, MAX_SKILL_LEVEL) == (1, 5)
    assert DEFAULT_LEARNING_GOAL_PRIORITY in {member.value for member in ProjectPriority}


def test_every_timestamp_column_defaults_to_now_in_the_database():
    """``now()`` server-side, so a row written outside the ORM is still stamped.

    Covers ``created_at``/``updated_at`` from
    :class:`~app.db.base.TimestampMixin` and the two hand-written ones on
    :class:`LearningActivity`.
    """
    for model, names in (
        (LearningGoal, ("created_at", "updated_at")),
        (Skill, ("created_at", "updated_at")),
        (LearningActivity, ("created_at", "occurred_at")),
    ):
        for name in names:
            assert _server_default(_columns(model)[name]) == "now()"


def test_the_columns_that_mean_absence_have_no_default():
    """``last_activity_at`` and ``completed_at`` are null or set, never defaulted.

    A ``last_activity_at`` defaulting to ``now()`` would make every newly
    created skill look active, and ``completed_at`` defaulting to anything at
    all would put a completion date on a goal nobody has completed.
    """
    for model, names in (
        (LearningGoal, ("completed_at", "estimated_effort_minutes")),
        (Skill, ("last_activity_at",)),
        (LearningActivity, ("duration_minutes", "source_type", "source_id")),
    ):
        for name in names:
            assert _server_default(_columns(model)[name]) is None, f"{model.__tablename__}.{name}"


# -- The append-only table ----------------------------------------------------


def test_learning_activities_carry_created_at_but_never_updated_at():
    """A recorded activity is a fact about a moment, and moments do not get revised.

    :class:`~app.db.base.TimestampMixin` stamps ``updated_at`` on every UPDATE,
    so using it here would assert a revision that cannot happen — and a reader
    who trusted the column would conclude the record had been rewritten. The
    other two tables *are* revised (a level is re-asserted, a goal progresses)
    and must carry the pair.
    """
    activity_columns = _columns(LearningActivity)
    assert "created_at" in activity_columns
    assert "updated_at" not in activity_columns
    for model in (LearningGoal, Skill):
        columns = _columns(model)
        assert "created_at" in columns
        assert "updated_at" in columns


# -- Check constraints --------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            LearningGoal,
            {
                "ck_learning_goals_progress_range": "progress >= 0 AND progress <= 100",
                "ck_learning_goals_completed_has_terminal_status": (
                    "completed_at IS NULL OR status = 'completed'"
                ),
            },
        ),
        (
            Skill,
            {
                "ck_skills_current_level_range": "current_level >= 1 AND current_level <= 5",
                "ck_skills_target_level_range": "target_level >= 1 AND target_level <= 5",
                "ck_skills_confidence_range": "confidence >= 0 AND confidence <= 100",
            },
        ),
        (
            LearningActivity,
            {
                "ck_learning_activities_duration_non_negative": (
                    "duration_minutes IS NULL OR duration_minutes >= 0"
                ),
            },
        ),
    ],
)
def test_the_check_constraints_are_named_and_say_what_they_say(model, expected):
    """Names *and* SQL, because a renamed constraint silently drops its meaning.

    The level ranges are the point of the whole file's first claim: a level is
    1-5 in the database, not merely in the request schema. ``confidence`` is
    checked because a percentage above 100 would make "how much evidence backs
    this" a claim that cannot be rendered.
    """
    assert _checks(model) == expected


def test_the_level_ranges_match_the_exported_bounds():
    """``MIN_SKILL_LEVEL``/``MAX_SKILL_LEVEL`` and the constraint agree.

    The bounds are exported for the gap service and the skill schema layer; if
    the pair drifted from the constraint, a payload the validator accepted could
    be rejected by the database on insert — a failure that surfaces as a 500 on
    an otherwise valid request. That the validator enforces the same pair is
    :func:`test_a_level_outside_one_to_five_is_refused`; this is the other
    direction, and neither half substitutes for the other.
    """
    checks = _checks(Skill)
    for column in ("current_level", "target_level"):
        assert f"{column} >= {MIN_SKILL_LEVEL} AND {column} <= {MAX_SKILL_LEVEL}" in checks.values()


def test_the_completion_stamp_constraint_holds_the_status_and_the_date_together():
    """``completed_at`` implies ``status='completed'``, never the reverse.

    Without it a goal could carry a completion date while reading as in progress,
    and every "how long does a goal take" figure would be computed from a date
    the goal does not itself claim.
    """
    assert (
        "completed_at IS NULL OR status = 'completed'"
        in _checks(LearningGoal)["ck_learning_goals_completed_has_terminal_status"]
    )


# -- Uniqueness ---------------------------------------------------------------


def test_a_skill_name_is_unique_per_account_and_never_globally():
    """``uq_skills_owner_name`` is ``(user_id, name)`` — the account is part of it.

    Two people may each track "Python"; a global unique index would make the
    second person's skill a conflict about a vocabulary they do not share. It is
    scoped to the account rather than left off because two rows called "Python"
    for one person would hold two ``current_level`` values that could disagree,
    and every gap read would have to guess which one the dashboard means.
    """
    assert _uniques(Skill) == {"uq_skills_owner_name": ("user_id", "name")}


def test_the_other_two_tables_declare_no_unique_constraint():
    """Goals and activities are not deduplicated by a constraint.

    A goal is the user's own wording and two goals with similar titles are two
    intentions; an activity is a fact about a moment, and there is no honest key
    that would say two records describe the same moment. Inventing one would
    silently drop evidence, which is the opposite of what this table is for.
    """
    assert _uniques(LearningGoal) == {}
    assert _uniques(LearningActivity) == {}


# -- Foreign keys and their deletion rules ------------------------------------


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            LearningGoal,
            {
                "user_id": ("users", "CASCADE"),
                "target_skill_id": ("skills", "SET NULL"),
                "project_id": ("projects", "SET NULL"),
                "note_id": ("notes", "SET NULL"),
            },
        ),
        (Skill, {"user_id": ("users", "CASCADE")}),
        (
            LearningActivity,
            {
                "user_id": ("users", "CASCADE"),
                "skill_id": ("skills", "SET NULL"),
                "goal_id": ("learning_goals", "SET NULL"),
            },
        ),
    ],
)
def test_the_deletion_rule_on_every_foreign_key_is_the_one_the_phase_intends(model, expected):
    """``ondelete`` asserted per column, because each is a different argument.

    ``users`` is CASCADE everywhere: a deleted account leaves nothing to own.
    ``learning_goals.target_skill_id`` is SET NULL because a goal can name a
    topic before the skill exists and a goal the user wrote outlives the skill
    row it referenced. ``learning_goals`` and ``learning_activities`` are the
    other half of the same argument.

    ``learning_activities.skill_id`` is **SET NULL**, and it used to be CASCADE.
    An activity row is an append-only record of a moment — that the person spent
    forty minutes on this — and CASCADE made deleting a skill silently destroy
    every one of those rows. The row was written down and then erased by an
    unrelated edit to a different table, which is the one thing a record of
    something that happened must not be. The surviving row keeps its timestamp,
    minutes, title and source, and says "no skill was named" rather than carrying
    a dangling id.

    The old CASCADE was justified by the activity becoming invisible, and that
    is no longer true: a null ``skill_id`` only excludes the row when a caller
    *narrows to one skill* (``LearningRepository.list_activities`` documents it,
    and ``skill_activities`` excludes them the same way). The owner's own
    activity feed still lists it, because the feed is about the account rather
    than about a skill page.
    """
    assert _foreign_keys(model) == expected


def test_the_polymorphic_source_pair_carries_no_foreign_key():
    """``source_id`` is a bare UUID, exactly as ``risks.entity_id`` is.

    One column has to serve a task, a note, a project and a repository, so it
    cannot carry a foreign key to any of them — and the label it is read with is
    ``source_type``, not the id itself. Making it a real FK would force a
    separate nullable column per source and give NEXUS four ways to be wrong
    about where an activity came from.
    """
    columns = _columns(LearningActivity)
    assert isinstance(columns["source_id"].type, PGUUID)
    assert not columns["source_id"].foreign_keys
    assert columns["source_type"].type.length == 32


def test_the_source_column_carries_every_subsystem_the_phase_names():
    """``manual``, ``task``, ``note``, ``project`` and ``repository`` all fit.

    The vocabulary is deliberately open — a Phase 10 subsystem deriving
    activities must not need a migration — and an unknown value is inert because
    it names no known subsystem rather than corrupting a row.
    """
    width = _columns(LearningActivity)["source_type"].type.length
    for source in ("manual", "task", "note", "project", "repository"):
        assert len(source) <= width


# -- Indexes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            LearningGoal,
            {
                "ix_learning_goals_user_id": (("user_id",), False),
                "ix_learning_goals_owner_status": (("user_id", "status"), False),
                "ix_learning_goals_owner_target_date": (("user_id", "target_date"), False),
            },
        ),
        (Skill, {"ix_skills_user_id": (("user_id",), False)}),
        (
            LearningActivity,
            {
                "ix_learning_activities_user_id": (("user_id",), False),
                "ix_learning_activities_user_occurred": (("user_id", "occurred_at"), False),
                "ix_learning_activities_skill_id": (("skill_id",), False),
                "ix_learning_activities_goal_id": (("goal_id",), False),
            },
        ),
    ],
)
def test_each_index_names_one_read_and_none_of_them_is_unique(model, expected):
    """Index name, columns and uniqueness, per table.

    Every one of these is a query the phase actually makes: the goal list by
    state, goals by deadline, the per-skill evidence count and last-30-days
    figure behind the gap, and the activity timeline every metric window reads.
    ``unique=False`` is asserted on all of them because a unique index here would
    be a business rule the phase never stated, and index names are load-bearing
    in ``tests/test_migration_ddl.py``.
    """
    assert _indexes(model) == expected


# -- No relationships, and no ORM-level surprises -----------------------------


@pytest.mark.parametrize("model", _MODELS, ids=lambda model: model.__name__)
def test_no_model_in_this_module_declares_a_relationship(model):
    """This codebase has zero ORM relationships and these three keep it that way.

    Navigation is an explicit owner-scoped ``select()`` in
    :mod:`app.repositories.learning`, because the lazy load a relationship
    invites is exactly the read that forgets to filter on ``user_id`` — and rule
    5 of the phase is that another account's goal is a 404, never a 403. A
    relationship would make it possible to write that bug at all.
    """
    relationships = [
        name
        for name, attribute in vars(model).items()
        if isinstance(attribute, RelationshipProperty)
    ]
    assert relationships == []


# -- Agreement with migration 0009 --------------------------------------------
#
# Rendered offline: Alembic writes a migration's SQL into a buffer instead of a
# connection when the context is configured with `as_sql=True`, so nothing here
# touches the test PostgreSQL that another agent may be holding.

_CONSTRAINT_LINE = re.compile(r"^(PRIMARY KEY|FOREIGN KEY|UNIQUE|CHECK|CONSTRAINT|EXCLUDE)\b")
_CREATE_TABLE = re.compile(r"CREATE TABLE (\w+) \((.*?)\n\)[;]?", re.S)
_CREATE_INDEX = re.compile(r"CREATE (UNIQUE )?INDEX (\w+) ON (\w+) \(([^)]*)\)(?: WHERE (.+?))?;")
_DROP_TABLE = re.compile(r"DROP TABLE (\w+);")
_FOREIGN_KEY = re.compile(r"FOREIGN KEY\((\w+)\) REFERENCES (\w+) \((\w+)\)([^\n]*)")


def _render(calls: str) -> str:
    """Run ``upgrade`` or ``downgrade`` on migration ``0009`` into a string.

    ``Operations.context`` installs the module-level ``alembic.op`` proxy the
    migration calls, and the ``output_buffer`` is where the offline ``impl``
    writes.
    """
    module = importlib.import_module("migrations.versions.0009_phase9_learning_career")
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": buffer},
    )
    with Operations.context(context):
        getattr(module, calls)()
    return buffer.getvalue()


def _rendered_normalise(ddl: str) -> str:
    """Collapse whitespace and drop what PostgreSQL adds on reflection.

    Three normalisations, each a difference of *rendering* rather than of schema.
    ``DEFAULT 'true'`` and ``DEFAULT true`` are the same default, and the same
    goes for a quoted ``DEFAULT '0'`` against ``sa.text("0")`` on the migration
    side — which is exactly the pair every counter in this schema produces. The
    ``::jsonb`` / ``::character varying`` suffixes are what ``pg_get_expr``
    prints.
    """
    collapsed = _whitespace(ddl)
    collapsed = re.sub(r"::\w+(\[\])?", "", collapsed)
    collapsed = re.sub(r"DEFAULT '(true|false)'", r"DEFAULT \1", collapsed)
    return re.sub(r"DEFAULT '(-?\d+)'", r"DEFAULT \1", collapsed)


def _ddl_columns(body: str) -> dict[str, str]:
    """``{column name: normalised DDL}`` for one ``CREATE TABLE`` body."""
    columns: dict[str, str] = {}
    for raw in body.split("\n"):
        line = raw.strip().rstrip(",").strip()
        if not line or _CONSTRAINT_LINE.match(line):
            continue
        columns[line.split()[0]] = _rendered_normalise(line)
    return columns


@pytest.fixture(scope="module")
def upgrade_ddl() -> str:
    """The SQL migration ``0009`` renders on the way up."""
    return _render("upgrade")


@pytest.fixture(scope="module")
def downgrade_ddl() -> str:
    """The SQL migration ``0009`` renders on the way down."""
    return _render("downgrade")


@pytest.fixture(scope="module")
def migrated_columns(upgrade_ddl: str) -> dict[str, dict[str, str]]:
    """``{table: {column: ddl}}`` for every table ``0009`` creates."""
    return {
        match.group(1): _ddl_columns(match.group(2))
        for match in _CREATE_TABLE.finditer(upgrade_ddl)
    }


@pytest.fixture(scope="module")
def model_ddl() -> dict[str, dict[str, str]]:
    """The same map, read from ``Base.metadata`` through :class:`CreateTable`.

    Rendered rather than read attribute by attribute because a column's
    ``server_default="true"`` renders as a quoted literal when SQLAlchemy is not
    given the column's type, which would report a difference against a migration
    that is byte-for-byte right.
    """
    dialect = postgresql.dialect()
    return {
        model.__tablename__: _ddl_columns(
            _CREATE_TABLE.search(str(CreateTable(model.__table__).compile(dialect=dialect))).group(
                2
            )
        )
        for model in _MODELS
    }


@pytest.mark.parametrize("table_name", LEARNING_TABLES)
def test_every_migrated_learning_column_matches_the_model(table_name, migrated_columns, model_ddl):
    """Type, nullability and server default agree, column by column.

    Both directions at once, because a dict equality is both: a column the
    model declares and the migration does not create is a read the product
    promises and the database cannot answer, and a column the migration creates
    that no model declares is invisible to every query in the product while
    ``alembic check`` reports no drift.
    """
    assert migrated_columns[table_name] == model_ddl[table_name]


def test_the_migration_creates_all_three_learning_tables(migrated_columns):
    """All three, and each with the columns this module declares.

    The career tables are created by the same revision and belong to another
    module, so the assertion is on presence of the learning three rather than
    on the whole created set — ``tests/test_migration_ddl.py`` owns the chain.
    """
    assert set(LEARNING_TABLES) <= set(migrated_columns)


def test_the_migrated_learning_tables_carry_no_updated_at(migrated_columns):
    """``learning_activities`` has ``created_at`` and nothing beside it.

    Asserted on the migration as well as the model because this is the one
    property that only a wrong ``create_table`` would break: the migration spells
    its DDL out rather than importing the models, so it is entirely possible for
    the two to disagree here while agreeing everywhere else.
    """
    activities = migrated_columns["learning_activities"]
    assert "created_at" in activities
    assert "updated_at" not in activities
    for table_name in ("learning_goals", "skills"):
        assert "updated_at" in migrated_columns[table_name]


def test_the_migration_writes_the_same_deletion_rules_as_the_models(upgrade_ddl):
    """CASCADE on ``user_id`` and ``skill_id``, SET NULL on every context link.

    The rendered ``FOREIGN KEY`` lines are compared as ``(child, parent, rule)``
    triples. The captured tail carries the statement's trailing comma with it,
    so the comparison is against the clause alone. Both directions matter: a
    ``CASCADE`` where a ``SET NULL`` was intended silently deletes a user's
    recorded trail, and a ``SET NULL`` where a ``CASCADE`` was intended leaves
    an activity with no subject that no page can reach.
    """
    rules = {
        (child, parent, rule.strip().rstrip(","))
        for child, parent, _referenced, rule in _FOREIGN_KEY.findall(upgrade_ddl)
    }
    assert ("user_id", "users", "ON DELETE CASCADE") in rules
    assert ("skill_id", "skills", "ON DELETE CASCADE") in rules
    for child, parent in (
        ("target_skill_id", "skills"),
        ("project_id", "projects"),
        ("note_id", "notes"),
        ("goal_id", "learning_goals"),
    ):
        assert (child, parent, "ON DELETE SET NULL") in rules


def test_the_migration_creates_the_same_named_indexes(upgrade_ddl):
    """Index name, table and columns, read off the rendered ``CREATE INDEX``.

    Only the learning tables' own indexes are compared; the career ones belong
    to another module. ``unique=False`` matters as much as the name: a unique
    index here would be a business rule the phase never stated, and index names
    are load-bearing in ``tests/test_migration_ddl.py``.
    """
    migrated = {
        match.group(2): (
            match.group(3),
            tuple(column.strip() for column in match.group(4).split(",")),
            match.group(1) is not None,
        )
        for match in _CREATE_INDEX.finditer(upgrade_ddl)
        if match.group(3) in LEARNING_TABLES
    }
    declared = {
        name: (model.__tablename__, columns, unique)
        for model in _MODELS
        for name, (columns, unique) in _indexes(model).items()
    }
    assert migrated == declared


def test_the_downgrade_drops_the_children_before_their_parent(downgrade_ddl):
    """``skills`` last, because two of the three tables reference it.

    ``learning_activities`` holds a foreign key onto both ``skills`` and
    ``learning_goals``, and ``learning_goals`` onto ``skills``, so dropping the
    parent first leaves children pointing at nothing and PostgreSQL refuses —
    a failure a fresh, empty test database is perfectly happy not to raise.
    """
    dropped = [match.group(1) for match in _DROP_TABLE.finditer(downgrade_ddl)]
    assert set(LEARNING_TABLES) <= set(dropped)
    assert dropped.index("skills") == len(dropped) - 1
    assert dropped.index("learning_goals") > dropped.index("learning_activities")


def test_the_migration_imports_no_model():
    """``0009`` imports nothing from ``app.models``.

    A migration that imports the ORM lets a later edit to a model silently
    rewrite history — the same reason ``0001`` through ``0008`` spell their DDL
    out. Matched against import *statements* rather than the bare string, because
    this revision's docstring legitimately cross-references
    :class:`~app.models.learning.Skill` by name; what it must not do is load it.
    """
    source = importlib.import_module("migrations.versions.0009_phase9_learning_career").__file__
    with open(source, encoding="utf-8") as handle:
        imports = re.findall(r"^\s*(?:from|import)\s+app\.models", handle.read(), re.M)
    assert imports == []


# ============================================================================ #
# Half two: the wire, as it is rendered                                       #
# ============================================================================ #

# -- Rule one: a nullable is never an absent key --------------------------------


@pytest.mark.parametrize("model", READ_SHAPES, ids=lambda model: model.__name__)
def test_no_nullable_field_carries_a_default(model):
    """A nullable that is also optional is how absence gets hidden.

    ``last_activity_at: datetime | None = None`` looks identical to the honest
    version on the page and behaves differently in the JSON: the first omits the
    key entirely for a row the service did not set, and the second always sends
    it. Required is therefore not pedantry — it is the only thing that guarantees
    the key is there.
    """
    nullable = {
        name: field for name, field in model.model_fields.items() if _is_optional(field.annotation)
    }
    defaulted = sorted(name for name, field in nullable.items() if not field.is_required())
    assert not defaulted, (
        f"{model.__name__}: {defaulted} admit None *and* have a default, so the key "
        "can be absent from the response. Declare them `T | None` with no default."
    )


@pytest.mark.parametrize("model", READ_SHAPES, ids=lambda model: model.__name__)
def test_the_set_of_nullable_fields_is_exactly_the_one_that_was_intended(model):
    """Which fields are nullable is a decision, and this pins it.

    A test that only checked "nullable implies required" would pass just as happily
    on a model where every column had quietly become ``T | None`` — turning an
    answer into a shrug. The expected sets below are the reasons Phase 9 makes:
    a duration nobody measured, a pointer that no longer resolves, a figure with
    an empty denominator. Adding a member here is adding a claim, and the next
    reader will see that the list grew.
    """
    actual = {name for name, field in model.model_fields.items() if _is_optional(field.annotation)}
    assert actual == EXPECTED_NULLABLE[model], (
        f"{model.__name__} nullable fields changed: {sorted(actual)}."
    )


@pytest.mark.parametrize("model", READ_SHAPES, ids=lambda model: model.__name__)
def test_every_nullable_key_is_present_in_the_serialised_payload(model):
    """The key is in the JSON even when its value is null.

    The counterpart of the check above, and the one that catches a default added
    later: a client reading ``payload["gap"]`` against a payload built by a
    server that omitted the key gets a ``KeyError``; a client reading
    ``payload.get("gap")`` gets ``None`` for both a null and a missing key, and
    cannot render the difference between "not measured" and "this server does not
    know".
    """
    payload = _minimal(model).model_dump(mode="json")
    missing = [
        name
        for name, field in model.model_fields.items()
        if _is_optional(field.annotation) and name not in payload
    ]
    assert not missing, f"{model.__name__} omitted the nullable keys {missing}."


def test_the_module_never_uses_optional():
    """``Optional[X]`` is banned at the source, not merely at the field.

    Pydantic normalises ``Optional[X]`` and ``X | None`` into the same internal
    representation, so a field-level check cannot tell which one the module used
    — and ``Optional[X] = None`` is precisely the shape that reintroduces a default
    on a nullable. Reading the annotations back out of the parsed module is the
    only place the distinction still exists.
    """
    offenders = [text for text in _annotations_in_source(_module_path()) if "Optional[" in text]
    assert not offenders, f"Phase 9 wire schemas use `T | None`, never `Optional[T]`: {offenders}"


@pytest.mark.parametrize("model", READ_SHAPES, ids=lambda model: model.__name__)
def test_every_field_documents_itself(model):
    """Every field carries description text, because these are read on ``/docs``.

    The description is not decoration here: the frontend developer consuming this
    surface routinely enumerates the enum values, and the only place those values
    are written down is here. An undescribed column is a closed set nobody can
    see.
    """
    undocumented = sorted(
        name for name, field in model.model_fields.items() if not (field.description or "").strip()
    )
    assert not undocumented, f"{model.__name__} leaves {undocumented} undocumented."


def test_a_patch_body_may_be_empty_where_a_response_may_not():
    """The write exemption is real, and it is why write models keep their defaults.

    A PATCH with an empty body is a well-formed request meaning "change nothing",
    and :class:`LearningGoalWriteBase` has to accept it. A response model with an
    empty body would be a server that failed to answer, and it must not be able to
    produce one. Same ``| None``, opposite requirement about defaults — which is
    why the rule above scopes itself to ``READ_SHAPES`` rather than being applied
    module-wide and quietly weakening the response side to fit the request side.
    """
    assert LearningGoalWriteBase().model_fields_set == set()
    assert LearningGoalWrite(title="Learn Rust").title == "Learn Rust"
    with pytest.raises(ValidationError):
        LearningGoalWrite()  # type: ignore[call-arg]


# -- The honesty control: a level never travels without its provenance ----------


def test_a_skill_level_cannot_be_rendered_without_saying_who_set_it():
    """``current_level`` and ``level_source`` are both required, together.

    A response carrying ``2`` and no source would let a card render ``2/5`` with
    nothing saying whether the person claimed it or NEXUS inferred it — which is
    the whole difference between "your self-assessed level is 2" and "you are not
    good at Machine Learning". Neither field has a default, so neither can be
    dropped on the way out.
    """
    fields = SkillRead.model_fields
    assert fields["current_level"].is_required()
    assert fields["level_source"].is_required()
    assert not _is_optional(fields["current_level"].annotation)


@pytest.mark.parametrize("source", ["user_defined", "system_estimate"])
def test_a_recognised_level_source_validates(source):
    """Both members of the closed set are accepted."""
    skill = SkillRead(**_payload(SkillRead, level_source=source))
    assert skill.level_source == source


def test_an_unrecognised_level_source_is_refused():
    """The column is a string, so a bad value is storable — and unrenderable.

    The only safe rendering of an unknown provenance is the cautious one, and a
    cautious renderer is not a guarantee. Refusing at the edge of the response is
    what makes "a level can never reach a screen without a stated provenance" a
    property of the schema rather than a review comment.
    """
    with pytest.raises(ValidationError):
        SkillRead(**_payload(SkillRead, level_source="vibes"))


@pytest.mark.parametrize("level", [0, 6, -1])
def test_a_level_outside_one_to_five_is_refused(level):
    """1-5 is enforced on the wire as well as by the database's check constraint.

    Five is enough to be useful and few enough that 3 and 4 mean different
    things. A ``0`` reaching a screen would be read as "no skill at all" and a
    ``6`` as mastery, and neither is a level anybody typed. The database's half of
    the same rule is :func:`test_the_level_ranges_match_the_exported_bounds`.
    """
    with pytest.raises(ValidationError):
        SkillRead(**_payload(SkillRead, current_level=level))


# -- A measured zero and an unmeasured figure are different answers --------------


def test_a_measured_zero_gap_stays_a_measurement():
    """``gap=0`` with ``available=True`` means the target is met.

    That is the most reassuring row the page can produce, and it must not be
    flattened into the same thing as an unmeasured gap just because both are
    numerically zero. It survives here because ``available`` is a separate field
    rather than something inferred from ``gap``.
    """
    gap = SkillGapRead(
        **_payload(
            SkillGapRead,
            available=True,
            reason_if_unavailable=None,
            gap=0,
            explanation="Target 3/5, current self-assessed 3/5. NEXUS recorded 4 related "
            "learning activities in the last 30 days.",
        )
    )
    assert gap.gap == 0
    assert gap.available is True
    assert gap.reason_if_unavailable is None


def test_an_unmeasured_gap_carries_no_number_pretending_to_be_one():
    """``available=False`` keeps the ``available``/reason pair the design rests on.

    The docstrings argue that the pairing is documented rather than *enforced* —
    a response model that raised on a service's missing reason would turn one bad
    row into a 500 for the whole page. So this asserts the contract the row is
    *expected* to keep, not a rejection: the fields are there, required and
    nullable, and an unmeasured row is representable without pretending to have a
    figure.
    """
    gap = SkillGapRead(
        **_payload(
            SkillGapRead,
            available=False,
            reason_if_unavailable="Not enough data to assess this yet.",
            gap=0,
            explanation="Target 4/5, current self-assessed 1/5. Nothing has been recorded "
            "against this skill yet.",
        )
    )
    assert gap.available is False
    assert gap.reason_if_unavailable
    assert gap.explanation


# -- Explanations carry their figures -------------------------------------------


def test_a_gap_explanation_without_a_figure_is_refused():
    """A digitless sentence is a judgement, and this model refuses one.

    "You have a gap in Machine Learning" renders perfectly well on a card, so a
    screenshot review sees a working page and not the missing number underneath
    it. The digit forces the sentence to name the levels and the evidence count,
    which is what makes it checkable against the rows beneath it.
    """
    with pytest.raises(ValidationError):
        SkillGapRead(
            **_payload(
                SkillGapRead,
                explanation="You have a gap in Machine Learning that NEXUS has noticed.",
            )
        )


def test_a_gap_explanation_carrying_a_figure_is_accepted():
    """The positive case, with the shape the contract names."""
    gap = SkillGapRead(
        **_payload(
            SkillGapRead,
            explanation="Target 4/5, current self-assessed 2/5. NEXUS recorded 6 related "
            "learning activities in the last 30 days.",
        )
    )
    assert gap.explanation.startswith("Target 4/5")


# -- Metrics: null is not zero ---------------------------------------------------


def _metric(**overrides: object) -> LearningMetricRead:
    """A :class:`LearningMetricRead` with every field filled from the example set."""
    return LearningMetricRead(**_payload(LearningMetricRead, **overrides))


def test_a_measured_zero_is_a_measurement_and_carries_no_reason():
    """``value=0.0`` with ``available=True`` is the arithmetic coming out at zero.

    Attaching the cold-start reason to it would be the exact conflation the phase
    forbids: a real zero rendered as though nothing had been measured. The reason
    is dropped rather than kept, so a client cannot print both.
    """
    metric = _metric(value=0.0, available=True, reason_if_unavailable="Not enough data.")
    assert metric.value == 0.0
    assert metric.reason_if_unavailable is None


def test_an_unavailable_metric_carries_a_reason():
    """``available=False`` always says why.

    This is the positive form of the same fact as a null value: "we looked, and
    there was nothing to look at". Without it the card would have to render an
    em-dash with no explanation, which is the reading the frontend is told to
    avoid.
    """
    metric = _metric(value=None, available=False, reason_if_unavailable=None)
    assert metric.available is False
    assert metric.value is None
    assert metric.reason_if_unavailable == "Not enough data to assess this yet."


def test_a_value_beside_a_refusal_is_dropped_rather_than_published():
    """A number cannot travel with a sentence saying there is none.

    Repaired rather than rejected: raising here would turn a service's bookkeeping
    slip into a 500 for the whole learning page, and the cost of the failure is a
    missing figure rather than a wrong claim.
    """
    metric = _metric(value=42.0, available=False, reason_if_unavailable="No goals yet.")
    assert metric.value is None
    assert metric.reason_if_unavailable == "No goals yet."


def test_a_metric_null_value_is_a_real_null_not_a_zero():
    """``value=None`` is a legal measurement outcome and is not coerced to 0.0."""
    metric = _metric(value=None, available=False, reason_if_unavailable="No activities.")
    assert metric.value is None
    assert metric.value != 0


# -- Series: zero-filled, and no invented time -----------------------------------


def _bucket(minutes: int | None) -> LearningActivityBucketRead:
    """One activity bucket with the given duration total (``None`` = unmeasured)."""
    return LearningActivityBucketRead(
        **_payload(
            LearningActivityBucketRead,
            activities=0 if minutes is None else 1,
            sessions=0 if minutes is None else 1,
            minutes=minutes,
        )
    )


def _series(
    *buckets: LearningActivityBucketRead, total_minutes: int | None = None
) -> LearningActivitySeriesRead:
    """A series over ``buckets``, with every envelope field filled."""
    return LearningActivitySeriesRead(
        **_payload(
            LearningActivitySeriesRead,
            buckets=list(buckets),
            total_minutes=total_minutes,
        )
    )


def test_a_quiet_window_is_zero_filled_rather_than_skipped():
    """Empty buckets arrive as zeroes, because a series that omits them compresses.

    A fortnight with three active days and a fortnight with fourteen would draw
    the same line if the gaps were dropped, which is a misreading of the data and
    not a presentational choice.
    """
    series = _series(_bucket(None), _bucket(None), _bucket(None))
    assert len(series.buckets) == 3
    assert [bucket.activities for bucket in series.buckets] == [0, 0, 0]


def test_a_series_with_no_recorded_duration_reports_no_time_at_all():
    """The contract's null-not-zero rule, applied to a summed duration.

    ``0`` minutes would claim time was measured and found to be nothing, which is
    a far more confident claim than "nobody said how long it took" — and inside a
    feature vector the two become indistinguishable to whoever consumes it later.
    """
    series = _series(_bucket(None), _bucket(None))
    assert series.total_minutes is None
    assert all(bucket.minutes is None for bucket in series.buckets)


def test_a_series_totals_only_the_buckets_that_measured_time():
    """A partial measurement totals partially, and the partial buckets stay null.

    The buckets with no duration are left null rather than being zeroed to make
    the sum work: zeroing them would be inventing a measurement in exactly the
    place the contract refuses one.
    """
    series = _series(_bucket(None), _bucket(40), _bucket(None))
    assert series.total_minutes == 40
    assert series.buckets[0].minutes is None
    assert series.buckets[2].minutes is None


# -- Tallies are complete ---------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "field_name", "expected"),
    [
        (
            LearningGoalListRead,
            "by_status",
            ("archived", "completed", "in_progress", "not_started", "paused"),
        ),
        (LearningActivityListRead, "by_type", ("study_session", "resource_viewed")),
        (SkillListRead, "by_level_source", ("system_estimate", "user_defined")),
        (SkillGapListRead, "by_level_source", ("system_estimate", "user_defined")),
    ],
)
def test_a_band_tally_always_carries_every_band(model, field_name, expected):
    """An empty tally still has every key, so the response shape never changes.

    This is the ``RiskListRead.by_severity`` rule applied to the learning
    tallies. A client reading ``by_status.completed`` with a fallback default
    would turn a missing key into the same number as an empty band, which is how a
    list header quietly stops reporting completed goals at all.
    """
    tally = getattr(_minimal(model), field_name)
    for band in expected:
        assert band in tally, f"{model.__name__}.{field_name} is missing {band!r}."
        assert tally[band] == 0


def test_a_caller_supplied_tally_keeps_its_counts_and_gains_the_empty_bands():
    """The validator fills gaps without discarding what was measured."""
    listing = LearningActivityListRead(
        **_payload(LearningActivityListRead, by_type={"study_session": 4, "made_up_type": 2})
    )
    assert listing.by_type["study_session"] == 4
    assert listing.by_type["resource_viewed"] == 0
    # A vocabulary member that reached a stored row before the client learned it
    # is carried through rather than dropped — losing the count would be worse
    # than carrying an unrenderable key.
    assert listing.by_type["made_up_type"] == 2


def test_the_tally_validators_copy_rather_than_mutate_the_callers_dictionary():
    """A dictionary the caller still holds must not be rewritten underneath it.

    The service builds the tally, hands it over, and often keeps it for the
    activity feed a moment later. Filling the empty bands in place would make the
    two disagree, and only the second reader would notice.
    """
    supplied = {"study_session": 1}
    LearningActivityListRead(**_payload(LearningActivityListRead, by_type=supplied))
    assert supplied == {"study_session": 1}


def test_an_empty_goal_list_still_summarises_itself():
    """The header sentence is composed by the list model, not left to each page.

    Two headers written by two pages would quote different totals for the same
    query, and the difference would only ever be visible on the one screen where
    both happened to be open.
    """
    listing = LearningGoalListRead(**_payload(LearningGoalListRead, total=0, summary=""))
    assert listing.summary == "No learning goals."


# -- Features ---------------------------------------------------------------------


def test_the_feature_vector_still_names_the_columns_the_contract_freezes():
    """The eight names are the contract with a future trainer; renaming one breaks it.

    A v2 must not be able to typecheck against v1 column meanings, which is why
    the version is a string the frontend carries as a closed union. That only
    works if the names underneath it do not move.
    """
    assert tuple(LearningFeatureValues.model_fields) == FROZEN_FEATURE_NAMES
    assert LEARNING_FEATURE_SCHEMA_VERSION == "learning_features.v1"
    # Checked on the field's default rather than on an instance: the default *is*
    # what a service that does not pass a version gets, and that is the path that
    # would silently ship a v1 payload labelled with something else.
    assert LearningFeatureVectorRead.model_fields["schema_version"].default == (
        LEARNING_FEATURE_SCHEMA_VERSION
    )


def test_an_account_with_no_goals_has_no_goal_progress():
    """``goal_progress`` is null, not ``0.0``.

    An account with no goals does not have goals that are zero percent complete,
    and a zero here would be read as the former.
    """
    features = LearningFeatureValues(
        **_payload(LearningFeatureValues, goal_progress=None, completion_rate=None)
    )
    assert features.goal_progress is None
    assert features.completion_rate is None


def test_no_feature_is_scored_and_nothing_is_called_a_confidence():
    """The feature names describe events, never a person's ability.

    There is no mastery, no decay and no composite on this surface, and adding a
    field whose unit is "how good is this person at learning" is the failure the
    whole phase is built to rule out. Asserted as a name check because the name is
    where such a field would be introduced.
    """
    forbidden = ("mastery", "proficiency", "competence", "ability", "score", "rating")
    offenders = [
        name
        for name in LearningFeatureValues.model_fields
        if any(word in name.lower() for word in forbidden)
    ]
    assert not offenders, f"Feature names {offenders} describe a person, not a recorded event."


# -- NEXUS's own columns are not client-writable ----------------------------------


@pytest.mark.parametrize("model", [SkillWrite, SkillUpdate])
def test_the_write_models_do_not_expose_the_columns_nexus_owns(model):
    """``level_source`` and ``confidence`` belong to the read path.

    A client that could set them would be able to file its own inference as a
    self-assessment, which is the one thing ``SkillLevelSource`` exists to
    prevent. They are absent from both write models and a ``SkillUpdate`` that
    sends one is a 422 naming the field.
    """
    assert "level_source" not in model.model_fields
    assert "confidence" not in model.model_fields
    with pytest.raises(ValidationError):
        SkillUpdate(level_source="system_estimate")  # type: ignore[call-arg]


def test_the_write_models_never_accept_an_owner_id():
    """Ownership is server-side; a body carrying a user id is refused, not honoured.

    ``SkillWrite``, ``SkillUpdate`` and ``LearningActivityWrite`` expose no
    ``user_id`` at all, so there is nothing for a client to fill in and nothing
    for the service to have to distrust.
    """
    for model in (
        SkillWrite,
        SkillUpdate,
        LearningGoalWrite,
        LearningGoalUpdate,
        LearningActivityWrite,
    ):
        assert "user_id" not in model.model_fields, f"{model.__name__} exposes user_id."


def test_a_patch_refuses_a_field_no_route_owns():
    """``extra="forbid"`` turns silence into an answer the client can act on.

    Under Pydantic's default a client sending an unknown field gets a cheerful
    200 with it dropped, and a caller reading that as "the change was applied" has
    believed something false about their own record.
    """
    with pytest.raises(ValidationError):
        SkillUpdate(nonsense_field=1)  # type: ignore[call-arg]
