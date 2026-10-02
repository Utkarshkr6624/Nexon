"""phase5_knowledge

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-15 00:00:00

Explicit DDL for the Phase 5 knowledge base: ``notes``, ``note_revisions``,
``concepts``, ``resources``, ``knowledge_links``, ``bookmarks``, ``documents``,
``categories``, and the ``note_tags`` / ``concept_tags`` association tables that
attach the per-user ``tags`` rows that already exist.

Models are deliberately NOT imported here — as in ``0001`` through ``0004`` — so
that a later change to ``app/models/`` cannot silently rewrite history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- documents ------------------------------------------------------------
    # Created first because `notes.document_id` points at it. Metadata only: no
    # blob, no content hash, no extracted text. Parsing a PDF is a later phase,
    # and a schema that pretended otherwise here would change when it lands.
    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        # Free-form, and nullable: an unclassified attachment is a normal state,
        # and the filter that would consume this value does not exist yet.
        sa.Column("document_type", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_documents_owner_id"),
        "documents",
        ["owner_id"],
        unique=False,
    )

    # -- notes ----------------------------------------------------------------
    op.create_table(
        "notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        # NOT NULL with an empty default rather than nullable: "a note with
        # nothing in it yet" is the continuous state of an autosaving editor,
        # and a nullable body would make every read null-check to render the same
        # blank page the default already renders.
        sa.Column("content", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        # Plain string, never a native enum; see `app/models/enums.py` for the
        # argument, and `NoteStatus` for the vocabulary.
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        # SET NULL, not CASCADE, and the only Phase 5 FK that is not an
        # ownership link: the document is metadata about a file, the note is the
        # knowledge. Losing the file reference must not delete the prose.
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notes_owner_id"), "notes", ["owner_id"], unique=False)
    op.create_index(op.f("ix_notes_document_id"), "notes", ["document_id"], unique=False)
    # Serves the note list, the phase's hottest read: `owner_id = ?`, usually
    # `AND status = ?`, always `ORDER BY updated_at DESC`. The single-column
    # owner index serves only the first term, so PostgreSQL would fetch every
    # note the user has ever written and sort in the heap. Equality on the first
    # two columns and a range on the third is the shape an index can serve, and
    # rows then arrive in the order the list renders anyway.
    #
    # Note what is NOT indexed: `title` and `content` are searched with ILIKE,
    # which a btree cannot serve (leading wildcard). This portable build has no
    # `pg_trgm`, so the search endpoint is bounded by LIMIT rather than served by
    # an index; a real deployment adds GIN trigram indexes here.
    op.create_index(
        op.f("ix_notes_owner_status_updated_at"),
        "notes",
        ["owner_id", "status", "updated_at"],
        unique=False,
    )
    # Deliberately NO unique constraint on `title`. Search must find notes by
    # title, which only means something if titles are shared vocabulary — and in
    # a knowledge base they are: a weekly review, a meeting note per attendee, a
    # draft that was saved three times under the same heading. A unique-per-owner
    # constraint would turn every one of those into a 409 at save time in an
    # editor that autosaves. Concepts, categories and bookmarks are the opposite
    # case and ARE constrained: those are vocabulary, where two rows spelled the
    # same are genuinely ambiguous and nothing can merge them.

    # -- note_revisions -------------------------------------------------------
    # `created_at` and no `updated_at`: a revision is immutable, exactly as
    # `audit_logs` is, and an `onupdate` rule on an append-only row would be a
    # lie. Each row is a point-in-time COPY of the note, not a diff — see
    # `app/models/knowledge.py::NoteRevision` for why a copy beats a patch chain.
    op.create_table(
        "note_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Carried on every revision so history is queryable per user without a
        # join, and so a revision is ownership-scoped in its own right. CASCADE:
        # these are the user's own words, not a record that must outlive them.
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # CASCADE from the note: a revision of a note that no longer exists
        # cannot be restored onto anything.
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_note_revisions_note_id"),
        "note_revisions",
        ["note_id"],
        unique=False,
    )
    # Serves two queries at once, which is why it is a composite rather than the
    # plain `note_id` index above: the revision list (newest first) and the prune
    # (`... WHERE note_id = ? AND created_at < cutoff`) both want this note's
    # revisions already ordered by time.
    op.create_index(
        op.f("ix_note_revisions_note_id_created_at"),
        "note_revisions",
        ["note_id", "created_at"],
        unique=False,
    )
    # Serves "this user's revision history across every note", which is a
    # different question from "this note's history" and cannot use the composite
    # above, because that one is led by `note_id`.
    op.create_index(
        op.f("ix_note_revisions_owner_id"),
        "note_revisions",
        ["owner_id"],
        unique=False,
    )

    # -- note_tags ------------------------------------------------------------
    # A join row onto the per-user `tags` that Phase 3 already defines. There is
    # deliberately no second tag table: two vocabularies per user cannot be
    # joined, filtered together or recoloured as a set. Composite PK so applying
    # the same tag twice is an idempotent no-op; CASCADE on both sides because an
    # association row means nothing without both of its rows.
    op.create_table(
        "note_tags",
        sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["note_id"], ["notes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("note_id", "tag_id"),
    )

    # -- concepts -------------------------------------------------------------
    op.create_table(
        "concepts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        # Uniqueness is scoped to the owner, so two users may each have a concept
        # called "async". Unlike two notes with the same title, two concepts with
        # the same name are genuinely ambiguous: nothing can merge them and every
        # "which notes explain this?" query would answer twice.
        sa.UniqueConstraint("owner_id", "name", name="uq_concepts_owner_id_name"),
        sa.PrimaryKeyConstraint("id"),
    )
    # The unique constraint above already indexes `owner_id` as its leading
    # column and would serve "all my concepts" alone; this narrower index is the
    # one PostgreSQL picks for the list query, as on `availability_rules`.
    op.create_index(op.f("ix_concepts_owner_id"), "concepts", ["owner_id"], unique=False)

    # -- concept_tags ---------------------------------------------------------
    op.create_table(
        "concept_tags",
        sa.Column("concept_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["concept_id"], ["concepts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("concept_id", "tag_id"),
    )

    # -- resources ------------------------------------------------------------
    op.create_table(
        "resources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.String(length=2048), nullable=True),
        # Eight values installation-wide is below the selectivity at which
        # PostgreSQL would choose an index over a scan, so `resource_type` gets
        # none — the same reasoning that leaves `status` unindexed on tasks.
        sa.Column(
            "resource_type",
            sa.String(length=16),
            server_default=sa.text("'other'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_resources_owner_id"), "resources", ["owner_id"], unique=False)

    # -- bookmarks ------------------------------------------------------------
    op.create_table(
        "bookmarks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        # Derived SERVER-SIDE from `url` by the service, never accepted from a
        # client: a caller-supplied domain is a caller-supplied lie, and it is
        # the shape a phishing bookmark would take. No generated column — deriving
        # a host is URL parsing, not SQL string surgery.
        sa.Column("domain", sa.String(length=255), nullable=True),
        # Archival is not a third editorial state, it is "not in the list". A
        # bookmark is otherwise exactly what its url says, and a bookmarked link
        # is a fact about the past worth keeping.
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        # Saving the same URL twice is always a mistake, never a merge: the two
        # rows would have identical titles with no way to tell them apart.
        #
        # Cost of this btree: PostgreSQL refuses an index row over ~2700 bytes. An
        # ASCII URL (what a percent-encoded one is) at 2048 fits in ~2080 bytes
        # with the owner key, so it holds — but a URL padded with multi-byte
        # characters can exceed the limit and be rejected on insert. If that ever
        # bites, the fix is a hash column alongside, not a wider btree.
        sa.UniqueConstraint("owner_id", "url", name="uq_bookmarks_owner_id_url"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_bookmarks_owner_id"), "bookmarks", ["owner_id"], unique=False)
    # `domain` gets no index: it is derived, so it is never a hand-written filter,
    # and at ~n domains per user it is not selective enough to be worth one.

    # -- categories -----------------------------------------------------------
    op.create_table(
        "categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        # NULL at the root. SET NULL rather than CASCADE: deleting a mid-tree
        # category should demote its children to roots, not delete them — they are
        # the user's content and the category was only their filing.
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["categories.id"], ondelete="SET NULL"),
        # A category that is its own parent is a cycle of length one, and a single
        # careless update can write it. One boolean expression over one row, so it
        # belongs in the database rather than in the service. A *deeper* cycle
        # (A -> B -> C -> A) takes a recursive walk to see and is the service's
        # job.
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_categories_parent_not_self",
        ),
        sa.UniqueConstraint("owner_id", "name", name="uq_categories_owner_id_name"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_categories_owner_id"), "categories", ["owner_id"], unique=False)
    # Serves the one query that matters — "the subcategories of this node" —
    # where the id is known before the query runs. The same index shape serves a
    # second question the tree rendering asks: a category's descendant count.
    op.create_index(op.f("ix_categories_parent_id"), "categories", ["parent_id"], unique=False)

    # -- knowledge_links ------------------------------------------------------
    # The polymorphic edge table. NO FOREIGN KEY on `source_id`/`target_id`, and
    # that absence is the deliberate cost of polymorphism: a FK points at exactly
    # one table, and which table these name is decided by `*_type`. So the
    # database cannot stop a dangling or cross-user edge, and
    # `KnowledgeService.create_link` must resolve BOTH endpoints through an
    # owner-scoped query BEFORE the write — a link whose target belongs to someone
    # else has to be refused *and write nothing*.
    #
    # `owner_id` CASCADEs, unlike `activity_events` which SET NULLs: an edge is
    # not history, it is a live relationship the user maintains, and an edge
    # whose owner has gone would be unreachable by every owner-scoped query *and*
    # undeletable by any of them — a row that leaks forever.
    op.create_table(
        "knowledge_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "link_type",
            sa.String(length=24),
            server_default=sa.text("'related_to'"),
            nullable=False,
        ),
        # Immutable like every other append-only row: an edge is not edited, it is
        # deleted and recreated with the other endpoint.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        # The same edge cannot be recorded twice: without it a double-click writes
        # two rows and every relationship listing renders the edge twice. Note
        # `link_type` is part of the identity, not an attribute of it — the same
        # pair may legitimately carry two relationships (a note both `references`
        # and `explains` a concept).
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "target_type",
            "target_id",
            "link_type",
            name="uq_knowledge_links_edge",
        ),
        # Both halves of the test are here deliberately. `CHECK (source_id <>
        # target_id)` alone would look right and be fine, but the naive way this
        # gets written wrong is `CHECK (source_type <> target_type)`, which
        # forbids EVERY note-to-note edge — the most common shape in a knowledge
        # base — because two notes of the same type are only distinguished by
        # their ids. Comparing the pairs refuses exactly the degenerate case and
        # leaves every real note-to-note link alone. Everything beyond a self-loop
        # is legal (`A -> B` alongside `B -> A` is meaningful), so there is no
        # cycle check here at all.
        sa.CheckConstraint(
            "NOT (source_id = target_id AND source_type = target_type)",
            name="ck_knowledge_links_no_self_edge",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_knowledge_links_owner_id"),
        "knowledge_links",
        ["owner_id"],
        unique=False,
    )
    # Serves the OUTBOUND half of the edge API:
    # `GET /knowledge/links?source_type=&source_id=` ("what does this note point
    # at?"). `owner_id` is deliberately not in the index: the leading pair is
    # already a single UUID probe, so including the owner would widen every entry
    # without narrowing the scan, and the ownership check happens on the handful
    # of rows this returns rather than during the lookup.
    op.create_index(
        op.f("ix_knowledge_links_source"),
        "knowledge_links",
        ["source_type", "source_id"],
        unique=False,
    )
    # Serves the BACKLINK half: `?target_type=&target_id=` ("what points at this
    # concept?"). The more valuable of the two in practice — a note is far more
    # often the target of someone else's reference than the source of one — and
    # without it every backlink panel is a sequential scan of the whole table.
    op.create_index(
        op.f("ix_knowledge_links_target"),
        "knowledge_links",
        ["target_type", "target_id"],
        unique=False,
    )


def downgrade() -> None:
    # Strict reverse dependency order. `knowledge_links` has no FK on its
    # endpoints, so it is independent and goes first on its own merit — nothing
    # can point at it. Then the leaves: the two association tables, then the
    # children that reference a parent (`note_revisions` -> `notes`, and `notes`
    # -> `documents`), then the tables nothing else references.
    #
    # Dropping a table takes its indexes and constraints with it, so none of the
    # `create_index` calls above are undone individually.
    op.drop_table("knowledge_links")
    op.drop_table("categories")
    op.drop_table("bookmarks")
    op.drop_table("resources")
    op.drop_table("concept_tags")
    op.drop_table("concepts")
    op.drop_table("note_tags")
    op.drop_table("note_revisions")
    op.drop_table("notes")
    op.drop_table("documents")