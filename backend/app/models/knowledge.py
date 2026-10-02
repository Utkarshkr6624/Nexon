"""Phase 5 knowledge base: notes, revisions, concepts, resources, links, bookmarks, documents and categories.

Eight owned tables, one polymorphic edge table, two association tables
-------------------------------------------------------------------
Everything a user writes down belongs to them and nothing else, so every one of
these tables carries ``owner_id`` and every read in
:mod:`app.repositories.knowledge` puts it in the ``WHERE`` clause. That single
fact is the security model of the phase: another user's id is not a permission
error, it is a row that does not exist, and it answers 404.

``knowledge_links`` is the one table here that is *not* an ordinary foreign-key
relationship, and it is worth stating the trade-off at the top because every
consequence below follows from it.

The polymorphism, and what it costs
-----------------------------------
A knowledge graph has notes, concepts and resources as nodes, which is six
ordered pairs of tables if each gets its own edge table — and a seventh the day
a fourth entity type appears. One table with a ``(type, id)`` endpoint instead:
``knowledge_links.source_type``/``source_id`` and ``target_type``/``target_id``.

The price is that **there is no foreign key on the endpoints**. A foreign key
points at exactly one table, and which table is precisely what
:attr:`KnowledgeLink.source_type` decides, so the database cannot express the
constraint. Three things therefore become the *service's* job and are the
service's job in
:meth:`app.services.knowledge_service.KnowledgeService.create_link`:

1. **Ownership.** Nothing stops an edge naming User B's note from User A's row.
   Both endpoints must be resolved through an owner-scoped query **before** the
   write — a link whose target belongs to someone else must be refused *and
   write nothing*, which means resolving first and inserting second, in one
   transaction.
2. **Dangling endpoints.** Deleting a note does not delete its edges: there is
   no FK for a cascade to hang off. The delete path has to remove them.
3. **Cycles and self-edges.** ``A -> B`` and ``B -> A`` are both legal and
   meaningful (a note explains a concept that references it back), so there is
   nothing to detect here; only the degenerate *self* edge is refused, and that
   one *is* enforceable in the database — see ``ck_knowledge_links_no_self_edge``.

What is enforced where
----------------------
The split is deliberate and is the same one the rest of the schema makes:

* **The database owns what is total and row-local.** A category that is its own
  parent and an edge that points at itself are both nonsense that no legitimate
  writer can produce, and both are one boolean expression over a single row.
  They are ``CHECK`` constraints here.
* **The service owns what needs a query.** Which table a link endpoint names,
  who owns it, whether a category re-parenting would close a cycle three levels
  up, and what a ``status`` string *means*. A ``CHECK`` can list the legal
  statuses, and :mod:`app.models.enums` deliberately declines to: the value
  vocabulary is written in one place and validated on every write path, because a
  value can arrive from an import or a script in another process and a
  constraint list in the DDL would be a second copy to keep in step.

Titles are not unique per owner
-------------------------------
:attr:`Note.title` carries **no** uniqueness constraint, and that is a decision
rather than an omission. Search has to find notes *by title*, which only means
anything if titles are shared vocabulary — and they are, in practice: a weekly
review note, a meeting note per attendee, a "notes" heading typed into three
drafts before any of them is real. A unique-per-owner constraint would turn
every one of those into a 409 at save time, in an editor that autosaves, for a
note the user cannot even see yet. Concepts, categories and bookmarks are the
opposite case and *are* constrained: those are vocabulary, where two rows
spelled the same are genuinely ambiguous and nothing can be merged for the user.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    KnowledgeLinkType,
    NoteStatus,
    ResourceType,
)

__all__ = [
    "DEFAULT_KNOWLEDGE_LINK_TYPE",
    "DEFAULT_NOTE_STATUS",
    "DEFAULT_RESOURCE_TYPE",
    "MAX_REVISIONS_PER_NOTE",
    "Bookmark",
    "Category",
    "Concept",
    "Document",
    "KnowledgeLink",
    "Note",
    "NoteRevision",
    "Resource",
    "concept_tags",
    "note_tags",
]

#: What a new note gets when the caller does not choose. ``draft``, matching
#: :class:`NoteStatus`'s own argument that "not asserted yet" is not a judgement.
DEFAULT_NOTE_STATUS = NoteStatus.DRAFT.value
DEFAULT_RESOURCE_TYPE = ResourceType.OTHER.value
#: ``related_to`` is the only member of :class:`KnowledgeLinkType` that means
#: nothing about the direction of the edge, so it is the one a caller can be
#: given by default without the default being a claim.
DEFAULT_KNOWLEDGE_LINK_TYPE = KnowledgeLinkType.RELATED_TO.value

#: How many revisions of one note are kept. See the growth note on
#: :class:`NoteRevision` for the choice and its consequence; exported because the
#: pruning decision belongs to the write path and the number belongs here, next
#: to the column it bounds.
MAX_REVISIONS_PER_NOTE = 50

#: Sized for a note title a human types; anything longer is a description.
_MAX_NOTE_TITLE_LENGTH = 300
#: A concept is a single named idea, so its name is tighter than a note title.
_MAX_CONCEPT_NAME_LENGTH = 200
#: A resource's title is a label for an external thing — a paper title, a repo
#: name — so it follows the note title's budget.
_MAX_RESOURCE_TITLE_LENGTH = 300
_MAX_BOOKMARK_TITLE_LENGTH = 300
#: `example.co.uk` plus room for an internationalised domain in punycode.
_MAX_DOMAIN_LENGTH = 255
#: RFC 3986 does not actually cap a URL, but a btree entry does: PostgreSQL
#: refuses an index row over roughly 2700 bytes. An ASCII URL (which is what a
#: percent-encoded one is) at this width fits in ~2080 bytes with the owner key
#: and header, so ``uq_bookmarks_owner_id_url` holds — but a URL padded with
#: multi-byte characters can exceed it and be *rejected on insert*. If a real
#: deployment hits that, the fix is a hash column alongside, not a wider btree.
_MAX_URL_LENGTH = 2048
_MAX_FILENAME_LENGTH = 255
_MAX_DOCUMENT_TITLE_LENGTH = 300
#: Free-form rather than a member of an enum: `document_type` classifies an
#: attachment ("spec", "invoice", "scan"), which is the user's vocabulary, not
#: the schema's. Wide enough for a compound phrase, short enough to stay a label.
_MAX_DOCUMENT_TYPE_LENGTH = 32
#: A category is a node in a tree, so its name has to read as one.
_MAX_CATEGORY_NAME_LENGTH = 120
#: Sized for the longest member of :class:`NoteStatus` (``published``),
#: :class:`KnowledgeEntityType` (``concept``) and :class:`ResourceType`
#: (``documentation``).
_MAX_ENUM_LENGTH = 16
#: Sized for the longest member of :class:`KnowledgeLinkType` (``related_to``)
#: plus headroom: this vocabulary is extensible by design, and widening the
#: column later is a migration for a reason that should not be "the column ran
#: out" on a table the graph query touches.
_MAX_LINK_TYPE_LENGTH = 24


class Note(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One piece of written knowledge, in whatever state its author left it."""

    __tablename__ = "notes"

    __table_args__ = (
        # The note list is the phase's hottest read: `owner_id = ?`, usually
        # `AND status = ?`, always `ORDER BY updated_at DESC`. The single-column
        # owner index serves only the first term, so PostgreSQL would fetch every
        # note the user has ever written and sort in the heap; with the
        # composite, the equality-then-range shape resolves inside the index and
        # rows already arrive in the order the list renders. `updated_at` last
        # because it is the only range term — a second range column would widen
        # the index without narrowing any scan.
        Index("ix_notes_owner_status_updated_at", "owner_id", "status", "updated_at"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(_MAX_NOTE_TITLE_LENGTH), nullable=False)
    # NOT NULL with an empty default rather than nullable: "a note with nothing in
    # it yet" is the state an autosaving editor produces continuously, and a
    # nullable body would make every read null-check to render the same blank
    # page the default already renders.
    content: Mapped[str] = mapped_column(
        Text,
        server_default=text("''"),
        nullable=False,
    )
    #: Optional one-paragraph abstract. Nullable and not derived: an extract is
    #: a different sentence from the note, and generating one would make the
    #: stored value disagree with the body whenever either is edited.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Plain string, not a native enum; see the module docstring of
    # `app.models.enums` for the whole argument and `NoteStatus` for the
    # vocabulary.
    status: Mapped[str] = mapped_column(
        String(_MAX_ENUM_LENGTH),
        server_default=DEFAULT_NOTE_STATUS,
        nullable=False,
    )
    #: The document this note was written from, when there was one. SET NULL,
    #: not CASCADE: the document is metadata about a file and the note is the
    #: knowledge, so losing the file reference must not delete the prose. Note
    #: that this is the *only* Phase 5 foreign key that is not an ownership link
    #: — every ownership link CASCADEs.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    # Index note: search reads `owner_id` and then `ILIKE`s title/content, which
    # no btree can serve — a leading-wildcard match is a scan. This build has no
    # `pg_trgm`/`unaccent`, so the search endpoint is bounded in SQL by `LIMIT`
    # rather than served by an index. A real deployment adds
    # `CREATE INDEX ... USING gin (content gin_trgm_ops)`; the reason for the
    # cap on `limit` is the same one.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Note id={self.id} title={self.title!r} status={self.status!r}>"

    @property
    def status_enum(self) -> NoteStatus | None:
        """The note's status as a member, or ``None`` if the row drifted.

        ``None`` rather than a fallback, for the reason
        :attr:`app.models.task.Task.status_enum` returns ``None``: a note
        silently reported as ``DRAFT`` when its row says something else is a lie
        the list view cannot detect. Validate on the way in with
        :func:`app.models.enums.validate_note_status`.
        """
        try:
            return NoteStatus(self.status)
        except ValueError:
            return None


class NoteRevision(UUIDPrimaryKeyMixin, Base):
    """A point-in-time **copy** of a note, taken before it changed.

    A copy, not a diff
    ------------------
    Each revision stores the whole title/content/summary as they stood. The
    alternative — storing a patch — looks like a saving and is not one: applying
    a chain of patches to reconstruct version N is O(N) work per read and turns
    one corrupted patch into every later version being unreadable, so the
    integrity of the whole history now depends on a line nobody has looked at
    since. A full copy makes each row independently restorable with a single
    ``INSERT ... SELECT``, which is what
    ``POST /knowledge/notes/{id}/restore-revision/{revision_id}`` does. The cost
    is storage proportional to the note, which is the trade the phase accepts.

    Immutable, so it declares ``created_at`` explicitly instead of using
    :class:`~app.db.base.TimestampMixin` — same reasoning, and the same shape,
    as :class:`app.models.audit.AuditLog`: an ``updated_at`` on an append-only
    row would be a lie, and an ``onupdate`` rule would rewrite the timestamp of
    the one row that must not change.

    Growth
    ------
    **Bounded: the newest :data:`MAX_REVISIONS_PER_NOTE` revisions of a note are
    kept and older ones are pruned.** Unbounded history on a table that grows
    one full copy of the note per keystroke-batch is the one place in NEXUS
    where storage grows without the user ever asking for anything, and the old
    end is the part nobody reads. The consequence, stated plainly: **restore
    only reaches within that window**, and after 50 edits the version from last
    March is gone. The newest revision is never pruned — the prune keeps the
    newest N by ``(created_at, id)``, so what survives is always a suffix that
    includes the most recent state, never a prefix that includes a stale one.
    """

    __tablename__ = "note_revisions"

    __table_args__ = (
        # Serves two queries at once, which is why it is a composite rather than
        # the plain `note_id` index below: `GET /notes/{id}/revisions` (newest
        # first) and the prune (`DELETE ... WHERE note_id = ? AND created_at <
        # cutoff`) both want this note's revisions already ordered by time. The
        # equality on `note_id` and the range on `created_at` are exactly the
        # shape an index can serve.
        Index("ix_note_revisions_note_id_created_at", "note_id", "created_at"),
    )

    note_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notes.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: Carried on every revision so history stays queryable per user without a
    #: join, and so a revision row is ownership-scoped in its own right. CASCADE
    #: from `users`: these are the user's own words, not a record that must
    #: outlive the account.
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(_MAX_NOTE_TITLE_LENGTH), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<NoteRevision id={self.id} note_id={self.note_id} title={self.title!r}>"


class Concept(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A named idea that notes, resources and other concepts attach to.

    Unique per owner, unlike :attr:`Note.title`. Two rows called "async" are
    genuinely ambiguous — nothing in the app can merge them and every "which
    notes explain async?" query would answer twice — so the second create is a
    409 and the UI offers the existing one instead.
    """

    __tablename__ = "concepts"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(_MAX_CONCEPT_NAME_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    __table_args__ = (
        # Same reasoning as `uq_tags_user_id_name`: uniqueness is scoped to the
        # owner, so two users may each have a concept called "async", and the
        # global vocabulary stays per-user like everything else in this phase.
        UniqueConstraint("owner_id", "name", name="uq_concepts_owner_id_name"),
    )

    # Index note: `uq_concepts_owner_id_name` above already indexes `owner_id` as
    # its leading column and would serve "all my concepts" on its own. The plain
    # index is kept because the column contract asks for it and because it is the
    # narrower of the two for the list query, exactly as on `availability_rules`.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Concept id={self.id} name={self.name!r}>"


class Resource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An external thing worth citing: a paper, a course, a repository."""

    __tablename__ = "resources"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(_MAX_RESOURCE_TITLE_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(_MAX_URL_LENGTH), nullable=True)
    # Eight values installation-wide: below the selectivity at which PostgreSQL
    # would choose an index over a scan, so it gets none. The same applies to
    # `notes.status`, `categories.name` and every other low-cardinality column in
    # this phase.
    resource_type: Mapped[str] = mapped_column(
        String(_MAX_ENUM_LENGTH),
        server_default=DEFAULT_RESOURCE_TYPE,
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Resource id={self.id} title={self.title!r} type={self.resource_type!r}>"

    @property
    def resource_type_enum(self) -> ResourceType | None:
        """The resource's type as a member, or ``None`` if the row drifted."""
        try:
            return ResourceType(self.resource_type)
        except ValueError:
            return None


class KnowledgeLink(UUIDPrimaryKeyMixin, Base):
    """One directed edge between two knowledge objects of any supported type.

    The polymorphic table, and the security model of the phase, are both
    described at length in the module docstring. The short version: **no
    foreign key on the endpoints, so the service resolves and ownership-checks
    both ids before it writes.**

    :attr:`owner_id` CASCADEs, like every other ownership link in the schema. It
    is worth noting that this is a deliberate choice against the
    ``activity_events`` rule: an edge is not history, it is a live relationship
    the user maintains, and an edge whose owner has gone is unreachable by every
    owner-scoped query *and* undeletable by any of them — a row that leaks
    forever and can never be cleaned up.
    """

    __tablename__ = "knowledge_links"

    __table_args__ = (
        # The same edge cannot be recorded twice. Without it, a double-click on
        # "link this concept" writes two rows and every relationship listing
        # renders the edge twice; with it, the second write is a 409 the service
        # turns into "that link already exists" rather than a 500. Note the
        # `link_type` at the end: the *same pair* may legitimately carry two
        # different relationships (a note both `references` and `explains` a
        # concept), so the type is part of the edge's identity, not an attribute
        # of it.
        UniqueConstraint(
            "source_type",
            "source_id",
            "target_type",
            "target_id",
            "link_type",
            name="uq_knowledge_links_edge",
        ),
        # A self edge is the one thing in this table that is nonsense rather than
        # merely redundant: "note X references note X" adds nothing a reader can
        # act on, and a graph renderer asked to draw it has to special-case the
        # loop.
        #
        # Both halves of the test are here deliberately. A naive
        # `CHECK (source_id <> target_id)` would forbid *every* note-to-note
        # edge — the single most common shape in a knowledge base — because two
        # different notes of the same type are only distinguishable by their
        # ids, and the ids do differ; but a naive
        # `CHECK (source_type <> target_type)` would forbid note-to-note too, and
        # one that dropped the types entirely would let concept X link to
        # concept X. Comparing the pairs only refuses the case where both halves
        # match, which is exactly the degenerate one. Everything *beyond* a
        # self-loop is legal and meaningful (`A -> B` alongside `B -> A`), so
        # there is no cycle check here at all.
        CheckConstraint(
            "NOT (source_id = target_id AND source_type = target_type)",
            name="ck_knowledge_links_no_self_edge",
        ),
        # Serves the outbound half of the edge API: `GET /knowledge/links
        # ?source_type=&source_id=` ("what does this note point at?"). The
        # leading pair is already selective — a UUID equality is a single index
        # probe — and every row that comes back belongs to one object's edge set,
        # which is small. `owner_id` is deliberately not in the index: it would
        # widen every entry without narrowing the scan, and the ownership check
        # happens on the handful of rows this returns, not during the lookup.
        Index("ix_knowledge_links_source", "source_type", "source_id"),
        # Serves the *backlink* half: `GET /knowledge/links
        # ?target_type=&target_id=` ("what points at this concept?"). This is the
        # more valuable of the two in practice — a note is far more often the
        # target of someone else's reference than the source of one — and it is
        # the reason the brief asks for it by name. Without it, every backlink
        # panel is a sequential scan of the whole edge table.
        Index("ix_knowledge_links_target", "target_type", "target_id"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: Which table :attr:`source_id` names. See
    #: :class:`app.models.enums.KnowledgeEntityType`.
    source_type: Mapped[str] = mapped_column(String(_MAX_ENUM_LENGTH), nullable=False)
    #: NOT NULL and deliberately *unconstrained* — see the module docstring.
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(String(_MAX_ENUM_LENGTH), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    link_type: Mapped[str] = mapped_column(
        String(_MAX_LINK_TYPE_LENGTH),
        server_default=DEFAULT_KNOWLEDGE_LINK_TYPE,
        nullable=False,
    )
    #: Immutable like every other append-only row here: an edge is not edited,
    #: it is deleted and recreated with the other endpoint.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<KnowledgeLink {self.source_type}:{self.source_id} "
            f"-[{self.link_type}]-> {self.target_type}:{self.target_id}>"
        )

    @property
    def link_type_enum(self) -> KnowledgeLinkType | None:
        """The edge's meaning as a member, or ``None`` if the row drifted."""
        try:
            return KnowledgeLinkType(self.link_type)
        except ValueError:
            return None


class Bookmark(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A saved URL. The smallest knowledge object, and the most numerous."""

    __tablename__ = "bookmarks"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    url: Mapped[str] = mapped_column(String(_MAX_URL_LENGTH), nullable=False)
    title: Mapped[str | None] = mapped_column(String(_MAX_BOOKMARK_TITLE_LENGTH), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: **Derived server-side from :attr:`url` by the service, never accepted from
    #: a client.** A caller-supplied domain is a caller-supplied *lie*: it would
    #: let a bookmark be filed under a domain it does not belong to, which is
    #: both a data-quality hole and the shape a phishing bookmark would take.
    #: There is no generated column because deriving a host is URL parsing, not
    #: SQL string surgery, and `urllib.parse.urlparse` in the service is the one
    #: implementation of "what is the host" that will not silently change.
    domain: Mapped[str | None] = mapped_column(String(_MAX_DOMAIN_LENGTH), nullable=True)
    #: Stamped when the bookmark is archived. Nullable rather than a status
    #: column: archiving is not a third editorial state, it is "not in the list",
    #: and a bookmark is otherwise exactly what its :attr:`url` says. Archival
    #: is distinct from deletion here in the way it is not for a note — a
    #: bookmarked link is a fact about the past, and deleting it is a separate,
    #: explicit act.
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        # Saving the same URL twice is always a mistake, never a merge: the two
        # rows would have identical titles and no way to tell them apart.
        # Scoped to the owner, like every other uniqueness here — two users may
        # each have bookmarked the same article. See the width note on
        # :data:`_MAX_URL_LENGTH` for what this btree costs.
        UniqueConstraint("owner_id", "url", name="uq_bookmarks_owner_id_url"),
    )

    # Index note: `domain` gets none. It is derived, so it is never a filter
    # someone writes by hand — "my bookmarks on this site" is a query the spec
    # does not ask for, and at ~n domains per user it would not be selective
    # anyway.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Bookmark id={self.id} url={self.url!r} domain={self.domain!r}>"


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Metadata about a file the user has attached to their knowledge.

    **Metadata only.** There is no blob, no object key, no content hash and no
    extracted text here; Phase 5 stores what a document *is* so that a note can
    point at it and so the ingestion phase has a row to attach to. Parsing a PDF
    is a later phase, and pretending otherwise here would mean either a half-built
    extractor in this one or a schema that has to change when the real one lands.
    """

    __tablename__ = "documents"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(_MAX_FILENAME_LENGTH), nullable=False)
    title: Mapped[str | None] = mapped_column(String(_MAX_DOCUMENT_TITLE_LENGTH), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Free-form on purpose — see :data:`_MAX_DOCUMENT_TYPE_LENGTH`. Nullable
    #: because an unclassified attachment is a normal state, not a hole: the
    #: alternative is a value the user has to pick before the feature that would
    #: filter on it exists.
    document_type: Mapped[str | None] = mapped_column(
        String(_MAX_DOCUMENT_TYPE_LENGTH),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Document id={self.id} filename={self.filename!r}>"


class Category(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A node in the user's tree of things, self-referencing through :attr:`parent_id`.

    A tree rather than a general graph because that is all the phase needs and a
    tree has a cheap, total answer to its two hard questions: a cycle is a cycle,
    and a parent is a parent. SET NULL rather than CASCADE on ``parent_id``:
    deleting a mid-tree category should demote its children to roots, not delete
    them — they are the user's content and the category was only their filing.
    """

    __tablename__ = "categories"

    __table_args__ = (
        # Same argument as `ck_tasks_parent_not_self`: a category that is its
        # own parent is a cycle of length one, and a single careless update can
        # write it. It is one boolean expression over one row, so it belongs in
        # the database — a check in the service is a check some other writer can
        # forget.
        #
        # A *deeper* cycle — A -> B -> C -> A — cannot be written here, because
        # it takes a recursive walk to see and a CHECK sees one row. That is the
        # service's job and is stated there, not silently assumed.
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_categories_parent_not_self",
        ),
        # Two categories with the same name under one owner are ambiguous and,
        # unlike two notes with the same title, nothing can merge them: the
        # children of one would have to move to the other, and which way is not
        # the app's decision to make.
        UniqueConstraint("owner_id", "name", name="uq_categories_owner_id_name"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(_MAX_CATEGORY_NAME_LENGTH), nullable=False)
    #: NULL at the root. Resolving one costs an owner-scoped lookup, which is why
    #: the service refuses a parent belonging to another user *before* the write
    #: rather than trusting the row afterwards.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    # Index note: `parent_id` serves the one query that matters — "the
    # subcategories of this node" — where the id is known before the query runs.
    # `name` gets no index of its own beyond the unique constraint above.

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Category id={self.id} name={self.name!r} parent_id={self.parent_id}>"


# Association tables, exactly like `task_tags`/`project_tags`: neither has a
# column of its own, so an ORM class would add a mapper and a lazy-load trap for
# no gain, and the repositories join these explicitly.
#
# Both reuse the **per-user `Tag` rows that already exist**. There is no second
# tag table in this phase: a second one would give one user two vocabularies that
# cannot be joined, filtered together or recoloured as a set, and `tags` is
# already scoped per user so it answers every query a knowledge tag would.
#
# Both CASCADE on both sides: an association row has no meaning without the note
# or the tag it joins, and neither should survive the other. Both use a composite
# primary key on the pair, so applying the same tag twice is an idempotent no-op
# rather than a duplicate every "tags on this note" listing renders twice.
#
# Neither has an index on `tag_id` alone, for the reason `task_tags` has none:
# every tag filter is combined with a note/concept-side term, so the plan walks
# the note's own index and probes by the `(note_id, tag_id)` prefix, which the
# primary key already serves.
note_tags = Table(
    "note_tags",
    Base.metadata,
    Column(
        "note_id", UUID(as_uuid=True), ForeignKey("notes.id", ondelete="CASCADE"), primary_key=True
    ),
    Column(
        "tag_id", UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    ),
)

concept_tags = Table(
    "concept_tags",
    Base.metadata,
    Column(
        "concept_id",
        UUID(as_uuid=True),
        ForeignKey("concepts.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id", UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    ),
)
