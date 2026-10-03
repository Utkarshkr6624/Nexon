"""learning_career_integrity

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-12 00:00:00

Three repairs to the Phase 9 schema, all of them cases where the DDL in ``0009``
described an invariant it did not actually enforce.

Models are deliberately NOT imported here — as in ``0001`` through ``0009`` — so
that a later change to ``app/models/`` cannot silently rewrite history.

What this migration changes
---------------------------
1. ``career_evidence`` deduplication, rebuilt as a *partial* unique index with
   ``NULLS NOT DISTINCT``.
2. ``learning_activities.skill_id`` changed from ``ON DELETE CASCADE`` to
   ``ON DELETE SET NULL``.
3. ``ix_activity_events_owner_created`` added — ``(user_id, created_at)`` on the
   table the activity feed is read from.

Defect 1: deduplication that does not deduplicate
-------------------------------------------------
``0009`` created ``uq_career_evidence_source_identity`` as a **table-level unique
constraint** over ``(user_id, evidence_type, source, project_id, skill_id,
repository_id)``. The prose beside it claimed that "several manually-added
``ACHIEVEMENT`` rows (all three FKs null) coexist, while a project-derived one
cannot be inserted twice". Only the first half of that is true.

PostgreSQL's default btree semantics are ``NULLS DISTINCT``: a null never equals
anything, so two rows whose ``skill_id`` is null do not collide even when every
other column is identical. The manual case works *because of* the very rule that
breaks the other half. A row derived from a project has ``project_id`` set and
``skill_id`` and ``repository_id`` null, so two derivations of the same project
produce tuples that differ by two nulls — which is to say they are equal under
nothing at all — and both insert cleanly. The constraint fires only when all
three foreign keys are non-null, which is the one case in which nothing needs it.

``NULLS NOT DISTINCT`` (PostgreSQL 15; this server is 16.2) makes the comparison
inside the index treat nulls as equal to each other, so a second project-derived
row is genuinely a duplicate of the first and is refused. It is **not** applied
to the whole table, because that would be wrong in the opposite direction: a plain
``UNIQUE NULLS NOT DISTINCT`` over all six columns would also collide two manual
achievements, which have all three foreign keys null and differ only in
``title``/``occurred_on``, and refusing the second of those is exactly what the
original design was trying to avoid. The user's ability to write down four
separate things they did is not a defect to be deduplicated away.

So the invariant is stated as a predicate rather than as the absence of nulls:
**a row that names at least one source is inserted at most once per identity.**

::

    CREATE UNIQUE INDEX uq_career_evidence_source_identity
        ON career_evidence (
            user_id, evidence_type, source, project_id, skill_id, repository_id
        )
        NULLS NOT DISTINCT
        WHERE project_id IS NOT NULL
           OR skill_id IS NOT NULL
           OR repository_id IS NOT NULL;

Two properties fall out of writing it this way rather than as a partial index
with a different column list:

* ``source`` stays in the key. A row NEXUS derived from a project and a row the
  user typed against the same project are different claims and both are allowed;
  without ``source`` the second would be refused as a duplicate of the first.
* ``title`` stays out of the key, for the reason ``0009`` gave: widening the key
  to include it would let a rename become a second row, and the row is
  identified by where it came from rather than by how it reads.

The alternative — dropping the FK columns from the index and adding a separate
unique index per source kind — was rejected because it produces three
invariants where the product has one, and because a fourth source added in Phase
10 would then need a migration to be covered at all.

**Existing duplicates are removed before the index is built.** The rule above was
never enforced, so a database that has been through ``0009`` may hold several
rows for one derived identity, and ``CREATE UNIQUE INDEX`` would abort the whole
upgrade on the first one it met. The ``DELETE`` keeps the **earliest** row of each
group — ordered by ``created_at`` then ``id``, so the choice is deterministic
rather than dependent on physical order — and removes the later re-derivations of
a claim the user has already got. Rows with all three foreign keys null are not
in scope and none is touched: the predicate above excludes them, and two manual
achievements are two achievements. This is the only statement in this file that
deletes rows that a user may have written, and it is bounded to rows that the
deduplication contract already said could not coexist.

Defect 2: a cascade that destroys evidence
------------------------------------------
``0009`` declared ``learning_activities.skill_id`` ``ON DELETE CASCADE`` and, in
the same file, wrote that "deleting a project or a skill leaves the recorded
trail standing". Both could not be true, and the constraint won. Deleting one
skill row silently removed every activity recorded against it — and because
``learning_activities`` has no ``updated_at``, there is no trace that anything
was ever removed. ``skills.evidence_count`` and ``last_activity_at`` would then
be counting rows that had already been destroyed, and
``app.services.learning.metrics`` — which reads the activity set as the record
of what was done — would report a smaller history without being able to say why.

``SET NULL`` makes the two columns on the table consistent with each other.
``learning_activities.goal_id`` already had it, and for the same reason: an
abandoned goal and a deleted skill are the same event, and neither should erase
the record that the user once worked on it. The activity survives as an
append-only fact with an unattributed subject, which is a state the column is
already ``NULL`` for and which the metrics layer already handles — a session
recorded before its skill row existed is a real session, not an orphan.

``user_id`` stays ``CASCADE``, and the asymmetry is the point rather than an
oversight. A row nobody can reach is an orphan no query can reach, so deleting
the account takes its rows; a row whose *subject* is gone is still a row in the
account's own history, and the trail outlives the thing it describes. That is the
same rule ``career_evidence``'s three foreign keys already follow and the same
rule ``activity_events`` follows in Phase 3.

Defect 3: a missing index on the hot path
-----------------------------------------
``app/repositories/activity.py`` orders every feed read by
``created_at DESC, id DESC`` over ``user_id = :user_id``. ``activity_events``
carried only ``ix_activity_events_user_id``, which can find the rows but cannot
supply their order, so PostgreSQL sorted the account's whole event history on
every page of the feed. ``activity_events`` is append-only and nothing prunes
it, so that sort grows without bound: measured at 0.19 ms / 2.8 ms / 13.3 ms for
1k / 20k / 100k events on a table with no retention job.

``ix_activity_events_owner_created`` over ``(user_id, created_at)`` lets the
planner walk the account's events already in timestamp order and stop at the
page size. The trailing ``id`` from the query is not indexed, which is
deliberate: it only breaks ties between events in the same microsecond, and
adding a third column to serve a tie-break would widen every insert into an
append-only table for the sake of an ordering that is already unique enough in
practice. ``created_at`` leads the sort, so it is the second column rather than
the first.

Downgrade
---------
Reverse dependency order, which for this file means the reverse of the upgrade:
the feed index is dropped first, the evidence constraint is restored before the
index that shares its name is dropped, and the ``CASCADE`` foreign key is put
back last. Restoring ``0009``'s deduplication necessarily re-permits the
duplicate it could not prevent — that is what ``0009``'s schema said, and a
downgrade returns the schema ``0009`` described rather than a better one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The name PostgreSQL gave the constraint ``0009`` created. Stated as a literal
#: rather than composed from the table and column names because the ``DROP`` has
#: to name the object that is actually there, and a spelling assembled at import
#: time would fail on deploy rather than in review.
_EVIDENCE_IDENTITY_CONSTRAINT = "uq_career_evidence_source_identity"
_ACTIVITY_SKILL_FOREIGN_KEY = "learning_activities_skill_id_fkey"

#: Which rows the deduplication rule applies to. A row is in scope when it names
#: at least one thing it was derived from; a manual achievement names none and is
#: outside the index by construction. Spelled as a literal here for the index's
#: ``postgresql_where``, and restated verbatim inside the ``DELETE`` below rather
#: than interpolated: a migration may not import ``app.models``, so the two copies
#: cannot be tied together by a name, and a literal a reader can check by eye is
#: worth more here than a constant that would hide the only statement in this file
#: that deletes rows.
_EVIDENCE_SOURCE_REFERENCED = (
    "project_id IS NOT NULL OR skill_id IS NOT NULL OR repository_id IS NOT NULL"
)


def upgrade() -> None:
    # ------------------------------------------------------------------
    # learning_activities.skill_id: CASCADE -> SET NULL
    # ------------------------------------------------------------------
    # Dropped and re-added rather than altered, because PostgreSQL has no
    # `ALTER CONSTRAINT ... ON DELETE`; the delete rule is a property of the
    # constraint, not of the column. The new constraint keeps its original name,
    # so the name a DBA sees in `pg_constraint` does not change shape underneath
    # them.
    op.drop_constraint(_ACTIVITY_SKILL_FOREIGN_KEY, "learning_activities", type_="foreignkey")
    op.create_foreign_key(
        _ACTIVITY_SKILL_FOREIGN_KEY,
        "learning_activities",
        "skills",
        ["skill_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # ------------------------------------------------------------------
    # career_evidence: a deduplication anchor that actually deduplicates
    # ------------------------------------------------------------------
    # First, because the unique index below refuses to build on a table that
    # already holds the duplicates the missing predicate let through. The window
    # function partitions with `IS NOT DISTINCT FROM` semantics, which is what
    # the index will enforce, so the two agree on what a duplicate is; `created_at`
    # then `id` makes the survivor the earliest claim rather than whichever row
    # the planner happened to return first.
    op.execute(
        sa.text(
            """
            DELETE FROM career_evidence
            WHERE id IN (
                SELECT id
                FROM (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY user_id,
                                            evidence_type,
                                            source,
                                            project_id,
                                            skill_id,
                                            repository_id
                               ORDER BY created_at, id
                           ) AS rederivation
                    FROM career_evidence
                    WHERE project_id IS NOT NULL
                       OR skill_id IS NOT NULL
                       OR repository_id IS NOT NULL
                ) ranked
                WHERE ranked.rederivation > 1
            )
            """
        )
    )
    # The table-level constraint is replaced rather than amended: its identity
    # is unchanged, its semantics are not. A `UNIQUE` constraint is implemented
    # as an index too, so the name has to be freed before the new index can
    # take it.
    op.drop_constraint(_EVIDENCE_IDENTITY_CONSTRAINT, "career_evidence", type_="unique")
    op.create_index(
        _EVIDENCE_IDENTITY_CONSTRAINT,
        "career_evidence",
        ["user_id", "evidence_type", "source", "project_id", "skill_id", "repository_id"],
        unique=True,
        # The half of the fix that the predicate alone cannot do. NULLS NOT
        # DISTINCT makes the two nulls of a project-derived row compare equal to
        # each other, which is the whole difference between "the duplicate is
        # refused" and "the duplicate is merely described".
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text(_EVIDENCE_SOURCE_REFERENCED),
    )

    # ------------------------------------------------------------------
    # activity_events: the feed's newest-first read
    # ------------------------------------------------------------------
    # The only index added here that is not repairing a Phase 9 table.
    op.create_index(
        "ix_activity_events_owner_created",
        "activity_events",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    # Reverse of the upgrade, in the order that keeps every intermediate state
    # legal:
    #
    # 1. The feed index goes first. It depends on nothing and shares no name
    #    with anything, so it is simply the first thing released.
    # 2. The evidence index goes before the constraint, because they share the
    #    name `uq_career_evidence_source_identity` and a constraint cannot be
    #    created while an index of that name exists.
    # 3. The `CASCADE` foreign key is put back last, restoring `0009`'s shape.
    #
    # Restoring `0009` re-permits the project-derived duplicate. That is
    # deliberate: a downgrade returns the schema the previous revision described,
    # and this file's argument for why that description was wrong is not something
    # a `downgrade()` can carry forward.
    op.drop_index("ix_activity_events_owner_created", table_name="activity_events")
    op.drop_index(_EVIDENCE_IDENTITY_CONSTRAINT, table_name="career_evidence")
    op.create_unique_constraint(
        _EVIDENCE_IDENTITY_CONSTRAINT,
        "career_evidence",
        ["user_id", "evidence_type", "source", "project_id", "skill_id", "repository_id"],
    )
    op.drop_constraint(_ACTIVITY_SKILL_FOREIGN_KEY, "learning_activities", type_="foreignkey")
    op.create_foreign_key(
        _ACTIVITY_SKILL_FOREIGN_KEY,
        "learning_activities",
        "skills",
        ["skill_id"],
        ["id"],
        ondelete="CASCADE",
    )
