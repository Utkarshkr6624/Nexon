"""Phase 9's career domain, asserted at both ends of its surface.

What this file covers
---------------------
Phase 9 puts a rule where it can be broken quietly in two different places, so
this file asserts both of them.

**The declared tables** — read from :class:`~app.db.base.Base`'s metadata, because
what matters is that the *declared* schema says what the phase intends: a wrong
column name, a flipped nullability, a missing constraint or a ``CASCADE`` where
the contract says ``SET NULL`` should fail here rather than in a rendered
``CREATE TABLE`` three files away. ``tests/test_migration_ddl.py`` and
``tests/test_migrations.py`` own the other half of that comparison — this file
does the part that needs no database at all. Every claim below is a property of
this phase rather than a schema convention, which is why these are the
assertions:

* **``career_profiles.user_id`` is unique.** That single column is what makes
  ``PUT /career/profile`` an upsert. Without it the same request twice leaves two
  profiles and ``GET /career/profile`` has to pick one.
* **Every ``career_evidence`` foreign key is ``SET NULL``.** The evidence is the
  trail, and a trail that vanishes when the project, skill or repository it
  describes is deleted is a view. ``user_id`` is the one exception and it is
  ``CASCADE``, because the profile is a statement *about* the account rather than
  a record that outlives it.
* **Nulls carry meaning.** ``career_experience.ended_on`` is null for a *current*
  role, not for an unknown one, and ``career_profiles`` is almost entirely
  nullable because an empty profile is a state the UI has to render. A default
  or a ``0`` in any of those places would replace an answer with a placeholder.
* **``links`` is ``NOT NULL`` with an empty-list default**, the only JSONB in the
  module, because "no links" and "links is null" are the same fact.
* **The uniqueness on evidence is six columns wide and leans on PostgreSQL not
  colliding nulls.** It is asserted column by column here so that a future edit
  cannot quietly add ``title`` and turn a rename into a second row.
* **No ``relationship()``** on any of the three tables — the lazy load a
  relationship invites is exactly the read that forgets to filter on ``user_id``.

**The wire schemas** in :mod:`app.schemas.career`, which carry one negative
promise — **there is no field here NEXUS could fill in that the user did not
supply** — and two mechanical ones: every nullable is a required key rather than
an absent one, and a figure that could not be computed is ``None`` rather than
``0``.

The negative promise is the hard one to test, because it is a claim about what
*is not* on the surface. So it is asserted three ways: by inspecting the write
models for any column the read models carry but the client must not set, by
refusing the attempt at validation time, and by checking that no field anywhere on
the module is named like a judgement. A generator does not announce itself with a
field called ``credential_verified``; it announces itself with ``source`` set by
a client and a column the service writes on the user's behalf.

The halves are kept in one file rather than two because they assert the same
rules from the two ends: the evidence columns are nullable in the database
*because* they are nullable on the wire, and ``source`` is ``NOT NULL`` with a
``'manual'`` default *because* no client may write it. Neither list is
intelligible without the other.

Neither half needs a database, so this file runs in the same pass as
``tests/test_developer_schema.py`` and never competes for the ``nexus_test``
advisory lock.
"""

from __future__ import annotations

import ast
import types
import typing
import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import CheckConstraint, Enum, String, UniqueConstraint, inspect

from app.db.base import Base
from app.models.career import (
    DEFAULT_CAREER_EVIDENCE_SOURCE,
    CareerEvidence,
    CareerExperience,
    CareerProfile,
)
from app.models.enums import CareerEvidenceType, CareerRecordKind
from app.schemas.career import (
    CAREER_FEATURE_SCHEMA_VERSION,
    CareerEvidenceListRead,
    CareerEvidenceRead,
    CareerEvidenceUpdate,
    CareerEvidenceWrite,
    CareerExperienceListRead,
    CareerExperienceRead,
    CareerExperienceUpdate,
    CareerExperienceWrite,
    CareerFeatureValues,
    CareerFeatureVectorRead,
    CareerProfileRead,
    CareerProfileWrite,
    CareerSummaryRead,
)

#: The three tables this module owns. Named rather than derived from metadata so a
#: fourth career table cannot appear without also being checked here.
CAREER_TABLES = ("career_profiles", "career_experience", "career_evidence")

_TABLES = {
    "career_profiles": CareerProfile.__table__,
    "career_experience": CareerExperience.__table__,
    "career_evidence": CareerEvidence.__table__,
}

#: The response shapes the two wire rules apply to. The write models are
#: deliberately excluded and the exemption is argued in
#: :func:`test_a_profile_put_may_be_empty_where_a_response_may_not` rather than
#: left implicit: a request body has to be able to omit a key, and "omitted"
#: means "leave this column alone" — a statement no response can make, because a
#: response that omitted a column would be a server that failed to answer.
READ_SHAPES: tuple[type[BaseModel], ...] = (
    CareerEvidenceListRead,
    CareerEvidenceRead,
    CareerExperienceListRead,
    CareerExperienceRead,
    CareerFeatureValues,
    CareerFeatureVectorRead,
    CareerProfileRead,
    CareerSummaryRead,
)

#: The six column names ``career_features.v1`` freezes. Spelled out rather than
#: imported, because the point of the wire tests is that the wire still says what
#: the contract said after somebody tidied the module.
FROZEN_FEATURE_NAMES = (
    "projects_completed",
    "project_activity",
    "repositories",
    "relevant_skill_evidence",
    "learning_activity",
    "portfolio_evidence_count",
)

#: The seven evidence types and the three record kinds, mirrored from the enums
#: so the test fails if a tally stops carrying one of them.
EVIDENCE_TYPES = (
    "achievement",
    "certification",
    "feature_shipped",
    "learning_milestone",
    "project_completed",
    "repository_activity",
    "skill_activity",
)
RECORD_KINDS = ("certification", "education", "experience")

#: Exactly which fields on each read shape admit null, and why. A list shape has
#: none — every one of its members is a count. Everything else is a field the user
#: may legitimately have left blank, a pointer that no longer resolves, or a
#: derived figure over an empty set of scanned repositories.
#:
#: These mirror the nullable column sets asserted in
#: :func:`test_every_descriptive_profile_column_is_nullable_but_links_is_not`,
#: :func:`test_a_null_end_date_means_current_rather_than_unknown` and
#: :func:`test_an_evidence_row_needs_a_type_a_title_and_a_date`; they are written
#: out again rather than derived, because a wire field that quietly became
#: required — or a column that quietly became ``NOT NULL`` — is exactly the drift
#: the pair of assertions exists to catch, and a derived expectation could not
#: fail.
EXPECTED_NULLABLE: dict[type[BaseModel], set[str]] = {
    CareerEvidenceListRead: set(),
    CareerEvidenceRead: {"description", "project_id", "skill_id", "repository_id"},
    CareerExperienceListRead: set(),
    CareerExperienceRead: {"organisation", "started_on", "ended_on", "description", "url"},
    CareerFeatureValues: {"project_activity"},
    CareerFeatureVectorRead: set(),
    CareerProfileRead: {"target_role", "target_domain", "headline", "summary", "location"},
    CareerSummaryRead: {"target_role", "latest_evidence_on"},
}


# -- Shared helpers ------------------------------------------------------------


def _column(table_name: str, column_name: str):
    """One column, looked up by name.

    Raises:
        KeyError: If the table has no such column — which is itself the failure
            the declared-schema half of this file exists to report, so it is left
            to surface as a plain lookup error rather than dressed up as a passing
            assertion.
    """
    return _TABLES[table_name].columns[column_name]


def _nullable(table_name: str) -> set[str]:
    """Column names in ``table_name`` that permit ``NULL``."""
    return {name for name, column in _TABLES[table_name].columns.items() if column.nullable}


def _not_null(table_name: str) -> set[str]:
    """Column names in ``table_name`` that forbid ``NULL``."""
    return set(_TABLES[table_name].columns.keys()) - _nullable(table_name)


def _unique_constraints(table_name: str) -> dict[str, tuple[str, ...]]:
    """``{constraint name: columns}`` for every ``UNIQUE`` constraint declared.

    Asserted against :class:`~sqlalchemy.UniqueConstraint` rather than against
    ``column.unique`` because a column-level flag and a table-level constraint
    are different objects with different names, and only the named one is a thing
    a reader of the migration can see.
    """
    return {
        constraint.name: tuple(column.name for column in constraint.columns)
        for constraint in _TABLES[table_name].constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name
    }


def _check_constraints(table_name: str) -> dict[str, str]:
    """``{constraint name: SQL text}`` for every ``CHECK`` constraint declared."""
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in _TABLES[table_name].constraints
        if isinstance(constraint, CheckConstraint)
    }


def _indexes(table_name: str) -> dict[str, tuple[str, ...]]:
    """``{index name: columns}`` for every index declared on the table."""
    return {
        index.name: tuple(column.name for column in index.columns)
        for index in _TABLES[table_name].indexes
    }


def _index(table_name: str, index_name: str):
    """The named :class:`~sqlalchemy.Index` on ``table_name``.

    Read as the object rather than through :func:`_indexes` because the
    deduplication index's behaviour lives in three flags a column list cannot
    show: ``unique``, ``postgresql_nulls_not_distinct`` and ``postgresql_where``.
    Those three *are* the rule — a unique index without the second is a rule that
    never fires, and one without the third is a rule that refuses things it
    should not — so a test that reads only the columns asserts a shape and calls
    it behaviour.
    """
    return next(index for index in _TABLES[table_name].indexes if index.name == index_name)


def _foreign_keys(table_name: str) -> dict[str, tuple[str, str]]:
    """``{column name: (referenced table, ON DELETE rule)}`` for the whole table."""
    rules: dict[str, tuple[str, str]] = {}
    for column in _TABLES[table_name].columns:
        for foreign_key in column.foreign_keys:
            referenced = foreign_key.target_fullname.split(".")[0]
            rules[column.name] = (referenced, foreign_key.ondelete or "")
    return rules


def _server_default(table_name: str, column_name: str) -> str:
    """The column's server default as the string SQLAlchemy holds.

    A string default is wrapped in a ``DefaultClause`` whose ``arg`` is the
    literal, so ``'manual'`` arrives as ``manual`` and ``[]`` as ``[]``. That is
    one level of representation and not of semantics — the rendered DDL adds the
    quotes back — so it is normalised here once rather than in every assertion.
    """
    column = _column(table_name, column_name)
    assert column.server_default is not None
    return str(column.server_default.arg)


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
    """A minimal valid value for ``annotation``, or ``None`` if it admits it."""
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
    return model(**{name: _example(field.annotation) for name, field in model.model_fields.items()})


def _payload(model: type[BaseModel], **overrides: object) -> dict[str, object]:
    """A full keyword payload for ``model``, with ``overrides`` applied."""
    payload: dict[str, object] = {
        name: _example(field.annotation) for name, field in model.model_fields.items()
    }
    payload.update(overrides)
    return payload


# ============================================================================ #
# Half one: what the three career tables declare                              #
# ============================================================================ #

# -- Registration -------------------------------------------------------------


def test_all_three_career_tables_are_on_the_shared_metadata():
    """They have to be on :class:`~app.db.base.Base`, or Alembic never sees them.

    A model declared against its own ``DeclarativeBase`` imports cleanly, passes
    every declared-schema test in this file and is invisible to autogenerate — its
    table is never created and ``alembic check`` reports no drift while the table
    does not exist.
    """
    for table_name in CAREER_TABLES:
        assert table_name in Base.metadata.tables


def test_every_table_carries_the_uuid_primary_key():
    """``id`` is a UUID supplied by the application.

    Generated in Python rather than by the database so an id is known before the
    row is flushed — the pattern every table in this schema uses.
    """
    for table_name in CAREER_TABLES:
        column = _column(table_name, "id")
        assert column.primary_key is True
        assert column.default.arg is not None


def test_all_three_career_tables_carry_both_timestamps():
    """Unlike Phase 8's observation tables, all three of these are revised.

    A commit or a finished scan is a fact about a moment and correctly has no
    ``updated_at``. A profile, a dated career record and a piece of evidence are
    all things the user edits, so :class:`~app.db.base.TimestampMixin` applies to
    every one of them — and a missing ``updated_at`` here would be the honest
    choice for a table of observations being used on a table of editable facts.
    """
    for table_name in CAREER_TABLES:
        assert "created_at" in _TABLES[table_name].columns
        assert "updated_at" in _TABLES[table_name].columns


def test_no_career_table_declares_a_relationship():
    """Zero relationships, in all three tables.

    Every read in the career layer is an explicit owner-scoped
    statement, because a relationship invites a lazy load that forgets the
    ``user_id`` filter — and another account's profile is a 404, never a 403.
    """
    for model in (CareerProfile, CareerExperience, CareerEvidence):
        assert list(inspect(model).relationships) == []


# -- career_profiles ----------------------------------------------------------


def test_the_profile_user_id_is_the_upsert_key():
    """Unique, not-null, cascading.

    This is the whole reason ``PUT /career/profile`` can be written as a lookup
    and an update. The constraint is asserted at the column as well as through
    the unique-constraint map because that is how it is declared — a future edit
    that moved it into ``__table_args__`` as a named constraint would still be
    correct behaviour but would no longer be this column's declaration, and the
    tests below say so out loud.
    """
    column = _column("career_profiles", "user_id")
    assert column.unique is True
    assert column.nullable is False
    assert [(fk.target_fullname, fk.ondelete) for fk in column.foreign_keys] == [
        ("users.id", "CASCADE")
    ]


def test_the_profile_carrying_no_unique_constraint_by_another_name():
    """Uniqueness is the column's, so no *named* duplicate exists.

    Declaring it twice — once as ``unique=True`` and once as a named
    ``UniqueConstraint`` — would build two independent constraints over the same
    column. The database would be no stricter than it already is, and the extra
    object would show up in a schema listing as a surprise.
    """
    assert _unique_constraints("career_profiles") == {}


def test_every_descriptive_profile_column_is_nullable_but_links_is_not():
    """A new account's profile is empty, and that is a state the UI renders.

    Five descriptive columns are nullable and ``links`` is not. ``links`` is the
    exception because "the user supplied no URLs" and "links is null" are the
    same fact spelled twice, and a reader should not have to pick between them.
    :data:`EXPECTED_NULLABLE` pins the same decision on ``CareerProfileRead``, and
    :func:`test_the_profile_links_are_always_a_list_and_never_null` pins it there
    too, so a column that flipped and a field that followed it fail separately.
    """
    assert _nullable("career_profiles") == {
        "target_role",
        "target_domain",
        "headline",
        "summary",
        "location",
    }
    assert _not_null("career_profiles") == {
        "id",
        "user_id",
        "links",
        "created_at",
        "updated_at",
    }


def test_profile_links_is_a_not_null_jsonb_list_defaulting_to_empty():
    """``JSONB``, ``NOT NULL``, server default ``[]``, Python default ``list``.

    The two defaults are both needed and they are not redundant: ``default=list``
    is what stops an ORM insert from sharing one mutable list between rows, and
    ``server_default`` is what gives a fixture or a bulk script that omits the
    column a list rather than a null.
    """
    column = _column("career_profiles", "links")
    assert type(column.type).__name__ == "JSONB"
    assert column.nullable is False
    assert str(_server_default("career_profiles", "links")) == "[]"
    assert column.default.arg.__name__ == "list"
    assert column.default.arg(None) == []


# -- career_experience --------------------------------------------------------


def test_the_experience_row_needs_a_kind_and_a_title_and_nothing_else():
    """Four NOT NULL columns, and two of them are the vocabulary.

    ``organisation``, both dates, the description and the URL are all optional,
    because a self-directed project has no issuer and an ongoing degree has no
    end date. Making any of them required would force the user to invent
    something to satisfy the database.
    """
    assert _not_null("career_experience") == {
        "id",
        "user_id",
        "kind",
        "title",
        "created_at",
        "updated_at",
    }


def test_a_null_end_date_means_current_rather_than_unknown():
    """``ended_on`` is nullable and ``started_on`` is too.

    Both halves matter: ``ended_on IS NULL`` is how an ongoing degree or a
    current role says so, and ``started_on IS NULL`` is how a record nobody
    could date is representable without a placeholder date that would be a
    fabricated one. That a client can read the same two answers back is
    :func:`test_an_ongoing_record_says_so_by_omitting_an_end_date`.
    """
    nullable = _nullable("career_experience")
    assert "ended_on" in nullable
    assert "started_on" in nullable


def test_the_experience_dates_are_checked_for_order_only_when_both_are_known():
    """One ``CHECK``, named exactly, and it is silent on the single-date cases.

    The predicate is asserted as a string because the name is what the migration
    and this test have to agree on, and the text is what explains the two
    ``IS NULL`` guards: the constraint only makes a claim where both dates are
    present.
    """
    checks = _check_constraints("career_experience")
    assert checks == {
        "ck_career_experience_dates_in_order": (
            "ended_on IS NULL OR started_on IS NULL OR ended_on >= started_on"
        )
    }


def test_the_experience_owner_cascades():
    """The account's history goes with the account."""
    assert _foreign_keys("career_experience") == {"user_id": ("users", "CASCADE")}


# -- career_evidence ----------------------------------------------------------


def test_an_evidence_row_needs_a_type_a_title_and_a_date():
    """``occurred_on`` is ``NOT NULL`` — undated evidence cannot go in a timeline.

    A placeholder date would be a fabricated one, and an evidence row with a null
    date would be invisible to every dated read while still counting toward the
    undated ones. The three pointers are nullable here *and* nullable on
    ``CareerEvidenceRead``; see :func:`test_an_evidence_pointer_need_not_resolve`
    for the other end of that pair.
    """
    assert _not_null("career_evidence") == {
        "id",
        "user_id",
        "evidence_type",
        "title",
        "occurred_on",
        "source",
        "created_at",
        "updated_at",
    }


def test_the_evidence_uniqueness_is_six_columns_and_does_not_include_the_title():
    """``uq_career_evidence_source_identity``, asserted key and flags.

    This is an **``Index``**, not a ``UniqueConstraint``, and the difference is
    the whole point: PostgreSQL will only apply a ``WHERE`` predicate to an
    index, so "at most one of these, but only once one of these three is set"
    cannot be written as a table constraint. That is why the deduplication rule
    is an object here rather than a ``UniqueConstraint`` — and why this test
    asserts it as one. :func:`test_the_evidence_dedup_is_the_only_uniqueness_on_the_table`
    keeps the two shapes from drifting back into each other.

    Three flags, and each is load-bearing:

    * ``unique=True`` — the rule itself.
    * ``postgresql_nulls_not_distinct=True`` — without it a project-derived row
      (one foreign key set, two null) collided with nothing, so a second
      derivation of the same project inserted cleanly. **This flag is the only
      reason the index refuses it**, and a unique index without it is exactly
      the bug: it looks like the rule and behaves as though it were not there.
    * ``postgresql_where`` — rows naming no source at all are outside it, which
      is what lets a person record several manual achievements.

    ``title`` is deliberately *not* in the key: adding it would let a rename of
    a project-derived row insert a second row, which is the failure this key
    exists to prevent. That no client can re-point the row in the first place is
    :func:`test_evidence_cannot_be_re_pointed_at_a_different_record`, and the
    rendered DDL is asserted in ``tests/test_migration_ddl.py``; the columns
    themselves are asserted here because that is the model a migration is
    written from.
    """
    index = _index("career_evidence", "uq_career_evidence_source_identity")
    columns = tuple(column.name for column in index.columns)

    assert index.unique is True
    assert columns == (
        "user_id",
        "evidence_type",
        "source",
        "project_id",
        "skill_id",
        "repository_id",
    )
    assert "title" not in columns
    assert dict(index.dialect_kwargs) == {
        "postgresql_nulls_not_distinct": True,
        "postgresql_where": (
            "project_id IS NOT NULL OR skill_id IS NOT NULL OR repository_id IS NOT NULL"
        ),
    }
    # Nothing else on the table claims uniqueness over the evidence rows, so a
    # second rule cannot quietly sit alongside this one and refuse something the
    # partial index deliberately lets through.
    assert _unique_constraints("career_evidence") == {}


def test_the_evidence_dedup_is_the_only_uniqueness_on_the_table():
    """The dedup key is the only unique thing on ``career_evidence``.

    Split from the test above so the *absence* of the old table-level
    ``UNIQUE`` is its own assertion. Two objects with the same name and
    different shapes is the state that confused this suite: the constraint could
    not enforce what its name promised, and the index that can is invisible to a
    reader scanning ``constraints`` for a ``UNIQUE``.
    """
    uniques = {
        index.name for index in CareerEvidence.__table__.indexes if index.unique
    }

    assert uniques == {"uq_career_evidence_source_identity"}


def test_every_evidence_link_is_set_null_so_the_trail_outlives_what_it_points_at():
    """``projects``, ``skills`` and ``git_repositories`` are all ``SET NULL``.

    This is the assertion the module is built around. ``CASCADE`` on any of the
    three would mean that deleting a project deleted the evidence that a project
    was completed, that deleting a skill deleted the record of using it, and that
    deleting a repository deleted what was observed in it. The evidence is the
    trail; a trail that disappears with the place it happened is a view.

    The referenced tables are named as strings rather than imported, which is why
    this checks ``target_fullname`` and not ``foreign_key.column`` — resolving the
    target would require ``app.models`` to have imported all three neighbour
    modules, and ``app/models/__init__.py`` is the single registration site.
    """
    assert _foreign_keys("career_evidence") == {
        "user_id": ("users", "CASCADE"),
        "project_id": ("projects", "SET NULL"),
        "skill_id": ("skills", "SET NULL"),
        "repository_id": ("git_repositories", "SET NULL"),
    }


def test_the_evidence_owner_cascades_and_is_the_only_cascade_on_the_table():
    """The account goes, its career history goes with it.

    Every other row here is a ``CASCADE`` on purpose, so this is the one place
    the two rules meet: the profile is a statement *about* the account, while the
    evidence records things that happened and outlive their sources.
    """
    cascades = {
        column
        for column, (_table, rule) in _foreign_keys("career_evidence").items()
        if rule == "CASCADE"
    }
    assert cascades == {"user_id"}


def test_the_evidence_source_defaults_to_manual_on_both_sides():
    """``NOT NULL``, server default ``'manual'``, and the same word in Python.

    The value is spelled as a module constant because it is half of the
    uniqueness key: a second spelling of "the user typed this" would be a source
    the constraint cannot recognise, which is the one column on this table where
    a typo would quietly allow a duplicate row instead of raising. That a client
    cannot spell it either is
    :func:`test_the_evidence_provenance_is_not_client_writable`.
    """
    column = _column("career_evidence", "source")
    assert DEFAULT_CAREER_EVIDENCE_SOURCE == "manual"
    assert str(_server_default("career_evidence", "source")) == "manual"
    assert column.nullable is False


def test_the_evidence_row_carries_no_check_constraint_at_all():
    """Deliberately empty, and asserted as empty.

    Nothing about this row is bounded: there is no level to fall outside the
    1-to-5 range and no count that could go negative. A ``CHECK`` invented here
    would be one more
    rule a future writer has to discover, and the vocabulary is already checked
    by ``validate_*`` before the write rather than by the database.
    """
    assert _check_constraints("career_evidence") == {}


# -- Indexes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("table_name", "index_name", "columns"),
    [
        ("career_profiles", "ix_career_profiles_user_id", ("user_id",)),
        ("career_experience", "ix_career_experience_user_id", ("user_id",)),
        ("career_evidence", "ix_career_evidence_user_id", ("user_id",)),
        ("career_evidence", "ix_career_evidence_user_occurred", ("user_id", "occurred_on")),
    ],
)
def test_a_career_index_exists_with_the_columns_the_reads_probe(table_name, index_name, columns):
    """Each index names one read, and the names are the contract's.

    The owner's rows on each table, and the evidence timeline ordered by date.
    The composite is a separate object rather than a longer ``ix_..._user_id``
    because the leading column differs, and the unique constraint on
    ``career_profiles.user_id`` is named separately again — it is a constraint
    here, not an index, and conflating the two is exactly the drift the
    migration tests exist to catch.
    """
    assert _indexes(table_name)[index_name] == columns


def test_no_career_table_declares_an_index_that_the_contract_does_not_name():
    """The declared set is exactly the contract's, in both directions.

    An index nothing reads is a write cost on every insert; a read nothing
    indexes is a sequential scan over the table a career profile grows fastest
    on. Neither is a large cost here, which is precisely why the set is fixed
    rather than left to whoever edits next.

    ``uq_career_evidence_source_identity`` is listed here as an index rather
    than as a constraint because that is what it now is. It joined this set when
    the deduplication rule moved off a table-level ``UNIQUE``, which could not
    carry the ``NULLS NOT DISTINCT`` behaviour or the partial predicate the rule
    needs; :func:`test_the_evidence_uniqueness_is_six_columns_and_does_not_include_the_title`
    asserts what it does.
    """
    assert set(_indexes("career_profiles")) == {"ix_career_profiles_user_id"}
    assert set(_indexes("career_experience")) == {"ix_career_experience_user_id"}
    assert set(_indexes("career_evidence")) == {
        "ix_career_evidence_user_id",
        "ix_career_evidence_user_occurred",
        "uq_career_evidence_source_identity",
    }


@pytest.mark.parametrize(
    ("table_name", "column_name", "vocabulary"),
    [
        ("career_experience", "kind", CareerRecordKind),
        ("career_evidence", "evidence_type", CareerEvidenceType),
    ],
)
def test_the_vocabulary_columns_are_plain_strings_rather_than_a_sqlalchemy_enum(
    table_name, column_name, vocabulary
):
    """``String(n)``, not ``Enum`` — and wide enough for every member.

    Adding a value to a native PostgreSQL enum needs ``ALTER TYPE ... ADD VALUE``,
    which historically could not run inside a transaction block — a new
    certification type would be a migration that can only half-apply. The
    trade-off is that the database will accept a value neither enum recognises,
    which is why every write goes through the ``validate_*`` helper.

    The second assertion is the one that catches a vocabulary growing past its
    column. ``risk.py`` already has the scar: one width sized for the risk status
    vocabulary truncated ``complete_blocked_task`` when a recommendation type
    outgrew it, and the rule fired correctly before dying in the database. Here
    the widest members are ``repository_activity`` (20 into 32) and
    ``certification`` (14 into 24), so there is room; this asserts it stays that
    way rather than trusting it.
    """
    column = _column(table_name, column_name)
    assert isinstance(column.type, String)
    assert not isinstance(column.type, Enum)
    assert column.nullable is False
    assert max(len(member.value) for member in vocabulary) <= column.type.length


# ============================================================================ #
# Half two: the wire, as it is rendered                                       #
# ============================================================================ #

# -- Rule one: a nullable is never an absent key --------------------------------


@pytest.mark.parametrize("model", READ_SHAPES, ids=lambda model: model.__name__)
def test_no_nullable_field_carries_a_default(model):
    """A nullable that is also optional is how absence gets hidden.

    ``ended_on: date | None = None`` and ``ended_on: date | None`` serialise
    differently for a row the service did not set: the first omits the key, the
    second sends null. Required is what guarantees the key is there — and
    ``ended_on`` being null is precisely how a profile says "this is current", so
    a client that could not tell the two apart could not render either.
    """
    nullable = {
        name: field for name, field in model.model_fields.items() if _is_optional(field.annotation)
    }
    defaulted = sorted(name for name, field in nullable.items() if not field.is_required())
    assert not defaulted, (
        f"{model.__name__}: {defaulted} admit None *and* have a default, so the key can "
        "be absent from the response. Declare them `T | None` with no default."
    )


@pytest.mark.parametrize("model", READ_SHAPES, ids=lambda model: model.__name__)
def test_the_set_of_nullable_fields_is_exactly_the_one_that_was_intended(model):
    """Which fields are nullable is a decision, and this pins it.

    A test that only checked "nullable implies required" would pass just as happily
    on a model where every column had quietly become ``T | None`` — turning an
    answer into a shrug. The expected sets below are the reasons Phase 9 makes: a
    field the user left blank, a pointer that no longer resolves, a derived figure
    over an empty set of scanned repositories.
    """
    actual = {name for name, field in model.model_fields.items() if _is_optional(field.annotation)}
    assert actual == EXPECTED_NULLABLE[model], (
        f"{model.__name__} nullable fields changed: {sorted(actual)}."
    )


@pytest.mark.parametrize("model", READ_SHAPES, ids=lambda model: model.__name__)
def test_every_nullable_key_is_present_in_the_serialised_payload(model):
    """The key is in the JSON even when its value is null.

    The counterpart of the check above, and the one that catches a default added
    later: ``payload.get("latest_evidence_on")`` returns ``None`` for both a null
    and a missing key, so a client cannot render the difference between "no
    evidence yet" and "this server does not tell us".
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
    representation, so a field-level check cannot tell which one the module used —
    and ``Optional[X] = None`` is exactly the shape that reintroduces a default on
    a nullable. The annotations are read back out of the parsed module, because a
    grep could not tell an annotation from one of this module's docstrings, which
    name ``Optional[T]`` several times in order to explain why it is banned.
    """
    import app.schemas.career as module

    assert module.__file__ is not None
    with open(module.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    annotations: list[str] = []
    for node in ast.walk(tree):
        annotation = getattr(node, "annotation", None)
        if annotation is not None and isinstance(node, (ast.AnnAssign, ast.arg)):
            annotations.append(ast.unparse(annotation))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns:
            annotations.append(ast.unparse(node.returns))
    offenders = [text for text in annotations if "Optional[" in text]
    assert not offenders, f"Phase 9 wire schemas use `T | None`, never `Optional[T]`: {offenders}"


@pytest.mark.parametrize("model", READ_SHAPES, ids=lambda model: model.__name__)
def test_every_field_documents_itself(model):
    """Every field carries description text, because these are read on ``/docs``.

    The description is not decoration here: the frontend developer consuming this
    surface routinely enumerates the closed vocabularies, and the only place those
    values are written down is here.
    """
    undocumented = sorted(
        name for name, field in model.model_fields.items() if not (field.description or "").strip()
    )
    assert not undocumented, f"{model.__name__} leaves {undocumented} undocumented."


def test_a_profile_put_may_be_empty_where_a_response_may_not():
    """The write exemption is real, and it is why write models keep their defaults.

    ``PUT /career/profile`` upserts, so an empty body has to be accepted: it
    produces a profile that exists and is blank, which is a state the UI renders
    and is not the same as having no profile row at all. A response model with an
    empty body would be a server that failed to answer, and must not be able to
    produce one — which is why the rule above scopes itself to ``READ_SHAPES``
    rather than being applied module-wide and quietly weakening the response side
    to fit the request side.
    """
    assert CareerProfileWrite().model_fields_set == set()
    assert _minimal(CareerProfileRead).id is not None


# -- The negative promise: nothing on this surface is NEXUS-generated ------------


@pytest.mark.parametrize(
    "model",
    [CareerProfileWrite, CareerExperienceWrite, CareerExperienceUpdate, CareerEvidenceWrite],
)
def test_no_write_model_accepts_an_owner_id(model):
    """Ownership is server-side; a body carrying a user id cannot become one.

    The write models expose no ``user_id`` at all, so there is nothing for a client
    to fill in and nothing for the service to have to distrust. A body that sends
    one anyway must not carry it any further: the update models reject it outright
    via ``extra="forbid"``, and the create models drop it, and in both cases
    ``user_id`` is absent from what the service would persist. Another account's
    profile is a 404, never a 403.
    """
    assert "user_id" not in model.model_fields, f"{model.__name__} exposes user_id."
    body = _payload(model, user_id=str(uuid.UUID(int=9)))
    if model.model_config.get("extra") == "forbid":
        with pytest.raises(ValidationError):
            model(**body)
        return
    assert "user_id" not in model(**body).model_dump(), f"{model.__name__} kept a client-set owner."


def test_the_evidence_provenance_is_not_client_writable():
    """``source`` is part of the row's identity, so no write model exposes it.

    ``uq_career_evidence_source_identity`` covers ``(user_id, evidence_type,
    source, project_id, skill_id, repository_id)`` and is the entire
    deduplication mechanism: a project-derived row cannot be inserted twice, while
    any number of manual rows can. A client that could write ``source='project'``
    could make two hand-typed rows collide, or impersonate a subsystem that never
    derived anything.
    """
    for model in (CareerEvidenceWrite, CareerEvidenceUpdate):
        assert "source" not in model.model_fields, f"{model.__name__} exposes source."
    with pytest.raises(ValidationError):
        CareerEvidenceUpdate(source="project")  # type: ignore[call-arg]


def test_evidence_cannot_be_re_pointed_at_a_different_record():
    """The three ``*_id`` pointers are absent from the update model.

    Re-pointing a row at another project would let a rename become a second
    record, which is the exact failure the uniqueness constraint exists to
    prevent. Only the words may be corrected — the person is allowed to be wrong
    about what they wrote, and not about where it came from.
    """
    for name in ("project_id", "skill_id", "repository_id"):
        assert name not in CareerEvidenceUpdate.model_fields
        with pytest.raises(ValidationError):
            CareerEvidenceUpdate(**{name: str(uuid.UUID(int=9))})


@pytest.mark.parametrize("model", [CareerProfileWrite, CareerExperienceWrite, CareerEvidenceWrite])
def test_no_write_model_carries_a_derived_or_scored_column(model):
    """Nothing on a request body may be something NEXUS would have to compute.

    ``user_id``, ``source`` and the id pointers are excluded above because they
    are pointers rather than inventions. Everything else a career write accepts is
    a transcription, and the test is that no field's name suggests otherwise —
    ``generated_summary``, ``profile_strength``, ``verified`` and ``rank`` are all
    the shape a generator would take.
    """
    forbidden = (
        "summary_generated",
        "generated",
        "strength",
        "score",
        "rank",
        "verified",
        "relevance",
        "imported",
        "profile_complete",
    )
    offenders = [
        name for name in model.model_fields if any(word in name.lower() for word in forbidden)
    ]
    assert not offenders, f"{model.__name__} accepts NEXUS-written columns {offenders}."


def test_a_manually_added_achievement_with_no_pointers_is_a_legitimate_row():
    """All three foreign keys null is valid, and is not an incomplete record.

    A hand-written achievement has nothing to point at, and the alternative — a
    required pointer — would force the user to attach a project they did not have
    one in. It is also the only way several achievements coexist, since nulls do
    not collide in a btree unique index.
    """
    evidence = CareerEvidenceRead(
        **_payload(
            CareerEvidenceRead,
            evidence_type="achievement",
            project_id=None,
            skill_id=None,
            repository_id=None,
        )
    )
    assert evidence.project_id is None
    assert evidence.source == "a" or isinstance(evidence.source, str)
    payload = evidence.model_dump(mode="json")
    assert payload["project_id"] is None


def test_an_evidence_pointer_need_not_resolve():
    """Every evidence foreign key is ``ON DELETE SET NULL``.

    Deleting a project must not delete the record that a project was completed, so
    a row's ``project_id`` is a claim that something existed rather than a
    guarantee it still does. The wire carries no implied liveness flag, and adding
    one would be a second answer to a question the row already answers. The
    database's half of this rule is
    :func:`test_every_evidence_link_is_set_null_so_the_trail_outlives_what_it_points_at`.
    """
    fields = CareerEvidenceRead.model_fields
    for name in ("project_id", "skill_id", "repository_id"):
        assert _is_optional(fields[name].annotation)
        assert fields[name].is_required(), f"{name} must be present even when nothing resolves."


def test_an_ongoing_record_says_so_by_omitting_an_end_date():
    """``ended_on`` being null is how "current" is expressed.

    It is a real answer and not an unrendered empty date, which is why the field
    is required-and-nullable rather than a date with a sentinel: the UI asks, and
    an ongoing degree or a current role is the common case.
    """
    record = CareerExperienceRead(**_payload(CareerExperienceRead, ended_on=None))
    assert record.ended_on is None
    assert record.started_on is not None


def test_no_field_anywhere_is_named_after_a_judgement():
    """Nothing on the module scores, ranks or qualifies the person it describes.

    Checked as a name sweep over every read model because that is where such a
    field would appear — ``profile_strength``, ``employability``, ``market_value``
    are the shapes this phase refuses. A name check is the closest thing to an
    assertion about intent that a test can make, and it is deliberately over-broad:
    a false positive is a name worth arguing about.
    """
    forbidden = (
        "strength",
        "score",
        "rank",
        "qualification",
        "employab",
        "market_value",
        "proficiency",
        "mastery",
        "rating",
        "reputation",
    )
    offenders = [
        f"{model.__name__}.{name}"
        for model in READ_SHAPES
        for name in model.model_fields
        if any(word in name.lower() for word in forbidden)
    ]
    assert not offenders, f"{offenders} describe a person rather than a record."


# -- Tallies are complete ---------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "field_name", "bands"),
    [
        (CareerEvidenceListRead, "by_type", EVIDENCE_TYPES),
        (CareerExperienceListRead, "by_kind", RECORD_KINDS),
    ],
)
def test_a_band_tally_always_carries_every_band(model, field_name, bands):
    """An empty tally still has every key, so the response shape never changes.

    This is the ``RiskListRead.by_severity`` rule applied to
    ``CareerEvidenceListRead.by_type``, which is the obvious case: a client
    reading ``by_type.certification`` with a fallback default would report "no
    certifications" both when the count is zero and when the key has gone missing,
    and only the second of those is a bug the page should be able to see.
    """
    tally = getattr(_minimal(model), field_name)
    for band in bands:
        assert band in tally, f"{model.__name__}.{field_name} is missing {band!r}."
        assert tally[band] == 0


def test_the_evidence_tally_fills_the_empty_bands_without_dropping_what_was_counted():
    """The validator fills gaps and keeps unknown keys.

    A vocabulary member that reached a stored row before the client learned it is
    carried through rather than dropped — losing the count would be worse than
    carrying a key the renderer will treat as unrecognised.
    """
    listing = CareerEvidenceListRead(
        **_payload(CareerEvidenceListRead, by_type={"certification": 2, "invented_type": 1})
    )
    assert listing.by_type["certification"] == 2
    assert listing.by_type["achievement"] == 0
    assert listing.by_type["invented_type"] == 1


def test_the_tally_validators_copy_rather_than_mutate_the_callers_dictionary():
    """A dictionary the caller still holds must not be rewritten underneath it.

    The service builds the tally, hands it over, and often keeps it for the
    profile summary a moment later. Filling the empty bands in place would make
    the two disagree, and only the second reader would notice.
    """
    supplied = {"certification": 1}
    CareerEvidenceListRead(**_payload(CareerEvidenceListRead, by_type=supplied))
    assert supplied == {"certification": 1}


def test_an_empty_evidence_list_still_summarises_itself():
    """The header sentence is composed by the list model, not left to each page.

    Two headers written by two pages would quote different totals for the same
    query, and the difference would only ever be visible on the one screen where
    both happened to be open.
    """
    listing = CareerEvidenceListRead(**_payload(CareerEvidenceListRead, total=0, summary=""))
    assert listing.summary == "No career evidence records."


def test_the_source_tally_is_not_zero_filled():
    """``by_source`` carries only the sources the account has used.

    The source vocabulary is deliberately open — a new subsystem must be able to
    derive evidence without a migration — so inventing keys for sources that never
    ran would be the schema asserting something false. ``manual_count`` is filled
    explicitly instead, because that one is the figure the design is obliged to
    be able to state.
    """
    listing = CareerEvidenceListRead(**_payload(CareerEvidenceListRead, by_source={"manual": 3}))
    assert listing.by_source == {"manual": 3}
    assert "project" not in listing.by_source


# -- Counts and measurements ------------------------------------------------------


def test_an_account_with_no_evidence_reports_zero_rather_than_unavailable():
    """``evidence_count = 0`` is a true and slightly uncomfortable fact.

    The user has entered no evidence yet. That is a real count of a real thing —
    their own input — and unlike a measured duration or a derived rate it needs no
    null escape. Making it ``None`` would turn an empty state into a gap in the
    data, which is the opposite of what it is.
    """
    summary = CareerSummaryRead(
        **_payload(
            CareerSummaryRead,
            evidence_count=0,
            record_count=0,
            manual_evidence_count=0,
            link_count=0,
            has_data=False,
        )
    )
    assert summary.evidence_count == 0
    assert summary.manual_evidence_count == 0
    assert summary.has_data is False


def test_project_activity_is_null_when_no_repository_has_ever_been_scanned():
    """The contract's own worked example of the null-not-zero rule.

    ``0`` would assert that a repository exists and carries no commits, when the
    truth is that nobody has looked at one. Inside a feature vector the two become
    indistinguishable to whoever trains on it later, which is the moment a
    fabricated zero does real damage.
    """
    features = CareerFeatureValues(**_payload(CareerFeatureValues, project_activity=None))
    assert features.project_activity is None
    assert features.project_activity != 0
    assert "project_activity" in features.model_dump(mode="json")


def test_the_summary_echoes_the_target_role_without_inventing_one():
    """``target_role`` is the user's words or it is null.

    NEXUS does not know what somebody wants next, so there is no code path that
    fills this from activity — and a header that printed a role nobody chose would
    be the first word on the page that NEXUS made up about the person using it.
    """
    assert CareerSummaryRead.model_fields["target_role"].is_required()
    payload = _payload(CareerSummaryRead, target_role=None)
    assert CareerSummaryRead(**payload).model_dump(mode="json")["target_role"] is None


def test_the_profile_links_are_always_a_list_and_never_null():
    """An empty list is the answer "no links supplied"; a null would be a third thing.

    There is no difference between "no links" and "links is null" that a reader
    should have to make, and NEXUS never fetches, checks or adds one. The column's
    ``NOT NULL`` and its two defaults are
    :func:`test_profile_links_is_a_not_null_jsonb_list_defaulting_to_empty`.
    """
    profile = CareerProfileRead(**_payload(CareerProfileRead, links=[]))
    assert profile.links == []
    assert "links" in profile.model_dump(mode="json")
    assert not _is_optional(CareerProfileRead.model_fields["links"].annotation)


def test_the_summary_distinguishes_no_profile_from_an_empty_one():
    """``has_profile`` is a separate fact from the counts being zero.

    An account that has never written a profile has no row to render; an account
    whose profile is blank renders as empty fields. Collapsing the two would make
    the first look like the second, and the UI's two messages are different.
    """
    summary = CareerSummaryRead(
        **_payload(CareerSummaryRead, has_profile=False, evidence_count=0, record_count=0)
    )
    assert summary.has_profile is False
    assert summary.evidence_count == 0


# -- Features ---------------------------------------------------------------------


def test_the_feature_vector_still_names_the_columns_the_contract_freezes():
    """The six names are the contract with a future trainer; renaming one breaks it.

    A v2 must not be able to typecheck against v1 column meanings, which is why
    the version is a string the frontend carries as a closed union. That only
    works if the names underneath it do not move.
    """
    assert tuple(CareerFeatureValues.model_fields) == FROZEN_FEATURE_NAMES
    assert CAREER_FEATURE_SCHEMA_VERSION == "career_features.v1"
    # Checked on the field's default rather than on an instance: the default *is*
    # what a service that does not pass a version gets, and that is the path that
    # would silently ship a v1 payload labelled with something else.
    assert CareerFeatureVectorRead.model_fields["schema_version"].default == (
        CAREER_FEATURE_SCHEMA_VERSION
    )


def test_the_features_describe_records_and_not_a_person():
    """No feature's unit is a person's ability.

    ``repositories`` counts work trees somebody registered; ``project_activity``
    counts recorded commits per completed project. Neither is a claim about what
    its owner can do, and a feature whose unit were "employability" would be a
    model in everything but name.
    """
    forbidden = ("ability", "employab", "skill_level", "seniority", "rating", "potential")
    offenders = [
        name
        for name in CareerFeatureValues.model_fields
        if any(word in name.lower() for word in forbidden)
    ]
    assert not offenders, f"Feature names {offenders} describe a person, not a recorded event."


# -- Patches refuse what no route owns ---------------------------------------------


def test_a_patch_refuses_a_field_no_route_owns():
    """``extra="forbid"`` turns silence into an answer the client can act on.

    Under Pydantic's default a client sending an unknown field gets a cheerful
    200 with it dropped, and a caller reading that as "the change was applied" has
    believed something false about their own career record.
    """
    with pytest.raises(ValidationError):
        CareerExperienceUpdate(nonsense_field=1)  # type: ignore[call-arg]


def test_the_update_models_are_patch_bodies_rather_than_full_replacements():
    """Every member of an update model is optional, because a PATCH is partial.

    If any were required, a client would have to resend the whole record to change
    one word of it — and, worse, a field the server had since defaulted would come
    back as an explicit value the user never chose.
    """
    for model in (CareerExperienceUpdate, CareerEvidenceUpdate):
        required = sorted(name for name, field in model.model_fields.items() if field.is_required())
        assert not required, f"{model.__name__} requires {required} in a PATCH body."
