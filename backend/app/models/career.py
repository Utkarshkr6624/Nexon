"""Phase 9 career tables: a profile, the records it is made of, and its evidence.

Nothing in this module was written by NEXUS
------------------------------------------
Every column on every row here is a transcription. ``career_experience`` holds
education, work history and certifications because *the user said so*, in the
words the user used, on the dates the user gave. There is no import that fills
these tables from a university register, a payroll, a certification body or a
scraped profile, and no service in this codebase writes a ``title``, an
``organisation``, a date or an ``ACHIEVEMENT`` the user did not supply. That is
the whole difference between a career module and a generator, and it is the
reason this file is this short: a schema wide enough to describe every possible
qualification is a schema that will eventually fill one in.

The corollary is that nothing here is *scored*. There is no column that says how
good a qualification is, how relevant it is to a role, or how many of them exist
— a count is a read, and the reads live in the service layer where they can name
what they counted. There is also no place in this module where an absence of
measurement could be confused with a measurement of zero, because no column here
holds a derived figure at all. The nearest thing is ``links``, and an empty
``links`` array means "the user supplied no URLs", which is an answer.

**``career_profiles.user_id`` is unique, and that is the upsert.** The profile is
one per account by definition — a person has one career, and a second row would
be a second *answer* to every question about it. So ``PUT /career/profile``
looks the row up by owner and updates it; there is no "create a new profile"
path that could leave the previous one behind. Without the constraint the same
request twice would produce two profiles, the profile read would have to decide
which one wins, and that decision would be a silent coin flip that changes the
page. The unique column is what makes ``GET /career/profile`` total. It is
enforced in the database rather than in the service because the service is not
the only writer — a fixture, a data script and a future endpoint are all equally
able to get this wrong, and this codebase's rule is that ownership is never
trusted from the client.

**Education, experience and certifications share one table.** They are
distinguished by ``career_experience.kind``, and three tables would have been
the alternative. The deciding question was whether the three kinds differ in
*shape* or only in *label*: they do not — all three are "a titled thing, at an
organisation, over a span of dates, with a description and a link" — and
splitting them would have tripled the router, tripled the schemas and made
"my whole career in one ordered list" a three-way union. What a certification
has that an education does not, the user can say in the description they wrote.

**Evidence is a claim the user makes about a record that may still exist.**
``career_evidence`` is the table that answers "what could I show someone", and
each row points at whatever it was drawn from: a project, a skill, a repository.
**Every one of those foreign keys is ``ON DELETE SET NULL``, never ``CASCADE``,
and that is the single most important decision in the module.** A ``CASCADE``
here would mean that deleting a project deleted the evidence of having finished
it — which inverts the entire point. The evidence is the trail, and a trail that
disappears when you stop watching the place it happened is not a trail; it is a
view, and the whole of rule 3 in this phase ("commits are not task completion",
"evidence is traceable") is that the record outlives the thing it records. The
same reasoning gives ``learning_activities.goal_id`` its ``SET NULL`` upstream.
The FKs are declared as strings rather than by importing :mod:`app.models.project`,
:mod:`app.models.skill` or :mod:`app.models.developer`: ``app/models/__init__.py``
is the single registration site, and a model module that reaches sideways for
its neighbours is a module whose table graph depends on import order.

**Deduplication is one index, and it leans on two PostgreSQL details.** A
derived evidence row — one NEXUS proposed from a project or a repository — can be
re-derived every time the source changes, so it needs an identity that is not the
row id. ``uq_career_evidence_source_identity`` covers ``(user_id,
evidence_type, source, project_id, skill_id, repository_id)`` and it is
**partial**: only rows that name at least one source are in it. Both halves of
that are load-bearing.

*The predicate* is what keeps manual achievements writable. Four separate things
a person did have all three foreign keys null, and the user is entitled to record
all four; putting a blanket unique constraint over the six columns would refuse
the second, and refusing it is not the product's decision to make.

*The ``NULLS NOT DISTINCT``* (PostgreSQL 15+) is what makes the index do what
the name says. A btree unique index is ``NULLS DISTINCT`` by default: nulls never
equal anything, so two rows that differ only by a null do not collide. That is
precisely the failure this index exists to prevent — a project-derived row has
``project_id`` set and ``skill_id``/``repository_id`` null, so a second
derivation of the same project differs from the first by two nulls and was
inserted happily. Inside a ``NULLS NOT DISTINCT`` index those two nulls are equal
to each other, the second derivation is a genuine duplicate of the first, and the
database refuses it. The constraint that shipped this behaviour was a table-level
``UNIQUE`` with no predicate and no such flag: it fired only when all three
foreign keys were non-null, which is the one case in which nothing needed it.

``source`` stays in the key, so a row NEXUS derived from a project and a row the
user typed against that same project are different claims and both are allowed.
``title`` stays out, for the reason ``0009`` gave: widening the key to include it
would let a rename become a second row — the row is identified by where it came
from, not by how it reads.

What is deliberately absent
---------------------------
* **No generator, no import, no enrichment.** Stated at the top because it is the
  one thing above that a future contributor will be tempted to add. NEXUS does
  not know the user's employer or whether their certificate is still valid.
* **No score, rank, weight or relevance column.** The vocabulary here is
  ``project_completed``, ``feature_shipped``, ``learning_milestone`` — a
  description of what happened. Anything that ordered those by importance would
  be a judgement about a person that the phase forbids and that no column could
  justify.
* **No derived or cached figures on the profile** — no activity counts, no
  "profile strength", no last-updated-from-git marker. The same argument
  :mod:`app.models.analytics` makes about weekly metrics: a stored copy is a
  second answer that can disagree with the dashboard. Every figure in Phase 9 is
  computed on read.
* **No per-link table behind ``links``.** A portfolio URL has no title to store,
  no ordering the user cares about and nothing NEXUS can measure about it. The
  alternative — ``career_profile_links`` with its own CRUD and its own cascade —
  would buy a join for no read that this product makes. ``links`` is a JSONB
  list, ``NOT NULL`` and defaulting to ``[]``, because an absent list and an
  empty one would otherwise be two spellings of the same fact.
* **No ``relationship()`` anywhere in this module.** Every read above this layer is
  written as an explicit owner-scoped statement, because the lazy load a
  relationship invites is exactly the read that forgets to filter on ``user_id``
  — and another account's profile is a 404, never a 403.
* **No soft deletes and no deletion reason.** Deleting a career record is a
  statement the user makes about their own history, and there is nobody to whom a
  hidden row would owe an explanation.

Enum storage follows the house rule from :mod:`app.models.enums`: ``kind`` and
``evidence_type`` are ``Mapped[str]`` in a ``String(n)``, validated in Python by
the ``validate_*`` helpers before the write, never a SQLAlchemy ``Enum`` and
never a native PostgreSQL enum type. The widths below are literals rather than
derived from the members because the migration spells them out too, and a column
that silently widened when a vocabulary member got longer would leave the two
sides of the schema disagreeing.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

__all__ = [
    "DEFAULT_CAREER_EVIDENCE_SOURCE",
    "EVIDENCE_SOURCE_IDENTITY",
    "EVIDENCE_SOURCE_REFERENCED_PREDICATE",
    "CareerEvidence",
    "CareerExperience",
    "CareerProfile",
]

#: What a piece of evidence is when nothing derived it. ``manual`` is the one
#: source value that is not a subsystem, and it is spelled here rather than in the
#: column so the service, the API schema and the repository all default to the
#: same word — a second spelling of it would be a source the uniqueness
#: constraint cannot recognise, which is the one field on this table where a typo
#: would quietly allow a duplicate row.
DEFAULT_CAREER_EVIDENCE_SOURCE = "manual"

#: A role the user is aiming at, in the words they would use in a sentence
#: ("Senior Backend Engineer"), not in an internal requisition's title.
_MAX_TARGET_ROLE_LENGTH = 200
#: The field they are aiming at — "backend", "data", "design". Deliberately short:
#: this is a label for grouping, and a phrase belongs in ``target_role``.
_MAX_TARGET_DOMAIN_LENGTH = 120
#: The one-line version of the profile, the length a list index or a search
#: result will show. The long version is ``summary``.
_MAX_HEADLINE_LENGTH = 200
#: A place, as the user writes it. A city is four words; "Remote (EU)" is seven;
#: there is no shape here worth a country/city pair, because NEXUS does not use it
#: to compute anything.
_MAX_LOCATION_LENGTH = 200
#: Sized for the longest :class:`~app.models.enums.CareerRecordKind` member
#: (``certification``, 14). 24 leaves room for the next member without a
#: migration.
_MAX_RECORD_KIND_LENGTH = 24
#: ``BSc Computer Science``, ``Senior Engineer``, ``AWS Solutions Architect
#: Associate``. A qualification's own name; anything longer is its description.
_MAX_EXPERIENCE_TITLE_LENGTH = 200
#: A university, a company, a certifying body — whichever issued the thing named
#: above. Nullable because a project or a self-directed course has no issuer.
_MAX_ORGANISATION_LENGTH = 200
#: 500 is the practical ceiling for a URL that includes its query string, and
#: this is the widest label in the module for that reason alone.
_MAX_URL_LENGTH = 500
#: Sized for the longest :class:`~app.models.enums.CareerEvidenceType` member
#: (``repository_activity``, 20). 32 leaves room for the next member.
_MAX_EVIDENCE_TYPE_LENGTH = 32
#: What the user says they did. One line, the same width as a task title
#: elsewhere in this schema.
_MAX_EVIDENCE_TITLE_LENGTH = 200
#: ``manual``, or the subsystem a derived row came from (``project``, ``skill``,
#: ``repository``). It is part of the uniqueness key below, which is why it is
#: NOT NULL with a default rather than left to a guess at read time.
_MAX_EVIDENCE_SOURCE_LENGTH = 64

#: The columns that identify one piece of evidence. A row is deduplicated by
#: what it was derived *from*, never by how it reads: ``title`` is excluded so a
#: rename cannot become a second row, and ``source`` is included so a row NEXUS
#: derived and a row the user typed against the same project can both exist.
#: Exported so the deduplication regression test asserts against the same tuple
#: the schema is built from rather than against a copy of it.
EVIDENCE_SOURCE_IDENTITY = (
    "user_id",
    "evidence_type",
    "source",
    "project_id",
    "skill_id",
    "repository_id",
)

#: Which rows the deduplication rule applies to. Spelled as a literal rather than
#: interpolated, because this text is part of the schema — the index definition is
#: generated from it — so building it by string formatting would mean a typo in a
#: tuple became a silently different index instead of a syntax error. It is the
#: same discipline ``app.models.risk`` applies to its live-status predicates, and
#: ``migrations/versions/0010_learning_career_integrity.py`` re-states it rather
#: than importing this, because a migration may not import ``app.models``.
EVIDENCE_SOURCE_REFERENCED_PREDICATE = (
    "project_id IS NOT NULL OR skill_id IS NOT NULL OR repository_id IS NOT NULL"
)


class CareerProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The one career summary this account has.

    A single row, because :attr:`user_id` carries a unique constraint — see the
    module docstring. Every descriptive column is nullable and that is the
    intended shape, not a half-built one: a new account has a profile with
    nothing on it yet, and an empty profile is a real state the UI has to render.
    ``links`` is the exception and is ``NOT NULL``, because there is no difference
    between "no links" and "links is null" that a reader should have to make.
    """

    __tablename__ = "career_profiles"

    __table_args__ = (
        # Redundant with the unique index below in the strict sense that the
        # unique constraint could serve this read on its own. It is declared
        # because the owner's profile is read on nearly every authenticated
        # request path in this module, and a named, obvious index is worth one
        # entry in a schema that is otherwise entirely about traceability.
        Index("ix_career_profiles_user_id", "user_id"),
    )

    #: The owner. Unique — this single column is what makes ``PUT
    #: /career/profile`` an upsert rather than a way to accumulate profiles, and
    #: it cascades: an account that is deleted takes its career history with it,
    #: which is the opposite of the rule the *evidence* FKs follow and is correct
    #: here because the profile is a statement *about* the account.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    #: What the user is aiming at. Never inferred from activity and never
    #: suggested as a completion — NEXUS does not know what the user wants next.
    target_role: Mapped[str | None] = mapped_column(String(_MAX_TARGET_ROLE_LENGTH), nullable=True)
    #: A grouping label for the target, not a controlled vocabulary. A closed set
    #: here would make a legitimate answer unenterable, and the only thing the
    #: column is for is letting the user filter their own evidence by direction.
    target_domain: Mapped[str | None] = mapped_column(
        String(_MAX_TARGET_DOMAIN_LENGTH), nullable=True
    )
    #: The one-line summary, in the user's words.
    headline: Mapped[str | None] = mapped_column(String(_MAX_HEADLINE_LENGTH), nullable=True)
    #: The long version. Free text, never generated, never summarised from other
    #: columns — a generated summary is a claim about a person that nothing in
    #: this schema can support.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Free text as written. Not split into country/city: nothing here consumes
    #: it, and a parsed location that disagreed with the string the user typed
    #: would be a second answer to "where are they".
    location: Mapped[str | None] = mapped_column(String(_MAX_LOCATION_LENGTH), nullable=True)
    #: Portfolio URLs, in the order the user listed them. A JSONB list and not a
    #: child table: there is no per-link title, no ordering that means anything
    #: and nothing NEXUS measures about a URL. ``default=list`` so an insert from
    #: Python cannot write a shared mutable, and ``server_default`` so a row
    #: written by a fixture or a script that omits the column still gets a list.
    links: Mapped[list[str]] = mapped_column(
        JSONB,
        server_default="[]",
        default=list,
        nullable=False,
    )


class CareerExperience(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One dated line on the profile: a course, a job, or a certification.

    ``kind`` is what separates them and nothing else does — see the module
    docstring for why they share a table. ``ended_on IS NULL`` is how a current
    role or an ongoing degree says so, which is a different fact from "finished
    on an unknown date"; the check constraint below only asserts that a start
    date is not after an end date when both are present, and deliberately says
    nothing about the single-date cases.
    """

    __tablename__ = "career_experience"

    __table_args__ = (
        Index("ix_career_experience_user_id", "user_id"),
        # A date range that runs backwards is a typo, and it would render as an
        # employment that ended before it started. Both columns may be null
        # (undated), and one may be null (current, or a start nobody knows), so
        # the predicate is only asserted where it is a claim about two known
        # dates.
        CheckConstraint(
            "ended_on IS NULL OR started_on IS NULL OR ended_on >= started_on",
            name="ck_career_experience_dates_in_order",
        ),
    )

    #: The owner. Cascades with the account, as everywhere in this schema.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: ``education``, ``experience`` or ``certification`` — a ``CareerRecordKind``
    #: member, validated in Python on write.
    kind: Mapped[str] = mapped_column(String(_MAX_RECORD_KIND_LENGTH), nullable=False)
    #: The user's own name for it. Not NULL even for a bare certification,
    #: because an untitled row on a profile is a record nobody can act on.
    title: Mapped[str] = mapped_column(String(_MAX_EXPERIENCE_TITLE_LENGTH), nullable=False)
    #: Who issued it or who it was done at. Null is normal and is not a gap: a
    #: self-directed project or an open-source contribution has no organisation,
    #: and inventing one is exactly the failure this phase forbids.
    organisation: Mapped[str | None] = mapped_column(
        String(_MAX_ORGANISATION_LENGTH), nullable=True
    )
    #: Null when the user did not say, and never filled in from anything NEXUS
    #: can observe.
    started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Null means *current* — an ongoing degree, a current role. It does not
    #: mean unknown; the UI asks, and an ongoing record is the common case.
    ended_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: The user's description of what it involved.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: A link the user supplied — the certificate, the programme, the company.
    #: Never discovered, never fetched.
    url: Mapped[str | None] = mapped_column(String(_MAX_URL_LENGTH), nullable=True)


class CareerEvidence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One thing the user wants to be able to point at.

    Evidence is a *claim with a provenance*: ``source`` says whether the user
    wrote it or a subsystem derived it, and the three nullable foreign keys say
    what it was derived from. A manually-added achievement has all three null,
    which is a legitimate row and not an incomplete one.

    **Every evidence foreign key is ``ON DELETE SET NULL``.** Deleting a project
    must not delete the record that a project was completed; the argument is in
    the module docstring and it is the reason this table is shaped the way it is.
    The tables are named as strings — ``projects``, ``skills`` and
    ``git_repositories`` are declared in other modules — because
    ``app/models/__init__.py`` is the single place that has to know about all of
    them.
    """

    __tablename__ = "career_evidence"

    __table_args__ = (
        # THE IDEMPOTENCY ANCHOR, and the whole deduplication mechanism. Two
        # independent pieces of PostgreSQL behaviour are doing the work, and
        # either alone leaves a hole:
        #
        # * `postgresql_where` keeps manual achievements out of the index. Four
        #   things one person did have all three foreign keys null; the index is
        #   about re-derivation, not about distinctness of achievements.
        # * `postgresql_nulls_not_distinct` is what makes it fire at all. A
        #   project-derived row has `skill_id` and `repository_id` null, and a
        #   default btree unique index treats those nulls as equal to nothing —
        #   so the second derivation of the same project inserted cleanly. Inside
        #   a NULLS NOT DISTINCT index they are equal to each other and the
        #   duplicate is refused.
        #
        # Same shape as `uq_risks_live_identity` — a partial unique index rather
        # than a table constraint — and for a related reason: both states of the
        # thing being deduplicated are worth keeping, so the rule has to be
        # scoped to the one state where it applies. `title` is deliberately NOT
        # part of the key: including it would let a rename become a second row.
        Index(
            "uq_career_evidence_source_identity",
            *EVIDENCE_SOURCE_IDENTITY,
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=EVIDENCE_SOURCE_REFERENCED_PREDICATE,
        ),
        Index("ix_career_evidence_user_id", "user_id"),
        # "My evidence, newest first" and every dated window in the career
        # summary. Leading column differs from the index above, so it is a
        # separate object rather than a longer one.
        Index("ix_career_evidence_user_occurred", "user_id", "occurred_on"),
    )

    #: The owner. Cascades with the account.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: A ``CareerEvidenceType`` member. ``REPOSITORY_ACTIVITY`` means commits
    #: were observed in a repository — not that a task was completed, and not
    #: that anything was finished. The vocabulary is the guarantee.
    evidence_type: Mapped[str] = mapped_column(String(_MAX_EVIDENCE_TYPE_LENGTH), nullable=False)
    #: The user's line for it, kept as written rather than generated from the
    #: linked record.
    title: Mapped[str] = mapped_column(String(_MAX_EVIDENCE_TITLE_LENGTH), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: A ``Date`` and not a timestamp: evidence is a thing that happened on a day
    #: — a course completed, a feature shipped — and precision to the second would
    #: be a precision nobody has. NOT NULL because undated evidence cannot be put
    #: in a timeline, and a placeholder date would be a fabricated one.
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    skill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skills.id", ondelete="SET NULL"),
        nullable=True,
    )
    repository_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("git_repositories.id", ondelete="SET NULL"),
        nullable=True,
    )

    #: ``manual``, or the subsystem that derived the row. NOT NULL with a server
    #: default, and part of the unique key above, so "where did this come from" is
    #: never a per-row guess at read time.
    source: Mapped[str] = mapped_column(
        String(_MAX_EVIDENCE_SOURCE_LENGTH),
        server_default=DEFAULT_CAREER_EVIDENCE_SOURCE,
        nullable=False,
    )
