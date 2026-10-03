"""Phase 9 — career intelligence, and why this package is one module.

The learning half of Phase 9 is split, and the split is the interesting part:

* :mod:`app.services.learning.metrics` and :mod:`app.services.learning.gaps` —
  pure functions from recorded rows to the eight explained metrics, the activity
  series and a :class:`~app.services.learning.gaps.SkillGap`. No database, no ORM,
  no clock, no request.
* :mod:`app.services.learning.service` — reads and writes storage, calls the two
  pure modules, assembles the wire shapes, emits the history events.

This package does not have that split, and the reason is worth stating rather than
leaving as an accident of scheduling.

Why career needs no pure module
-------------------------------
A skill gap is a **judgement**: a level, a confidence, and a sentence about how far
someone is from where they said they wanted to be. Extracting it is what lets the
judgement be asserted against a fixed tuple of inputs with no database, and what stops
it drifting once the arithmetic underneath it changes. There is no comparable
judgement on the career page. Every figure here is a count over rows the user typed
or a subsystem recorded — records on file, evidence by type, how much of the profile
the user wrote by hand, commits on repositories linked to completed projects. The
arithmetic is ``len()``, a distinct count and one window. A pure module here would be
a module of counting functions that adds an import and buys no isolation at all.

And the rules that *do* govern this page are **negative** — the things the service
refuses to do. It never writes a second profile, never writes a certification,
employer, date or achievement the user did not supply, never answers a missing
measurement with a zero, never returns a foreign row as a 403. A refusal is not a
function you can call and it cannot be lifted out of the service without leaving the
write behind it, so the place those rules belong is the code that performs the write,
and this package's docstring is where the argument lives.

What the boundary *is*, then
----------------------------
Storage and wire shapes. :mod:`app.services.career.service` is constructed from
repositories rather than from a session, exactly like
:class:`~app.services.developer.service.DeveloperIntelligenceService` and
:class:`~app.services.learning.service.LearningIntelligenceService`, so every
statement it causes is owner-scoped in the repository layer and the service holds no
state between calls. Its public methods are keyword-only and take ``owner: User`` —
the account comes from the session on the server and is never a request argument,
which is why another account's profile, record or piece of evidence is a **404 and
never a 403**.

Two readings are named in the frozen contract and both live here:
:meth:`~app.services.career.service.CareerIntelligenceService.summary` and
:meth:`~app.services.career.service.CareerIntelligenceService.features`. The second
is an **extractor, not a model**: six named numbers under ``career_features.v1`` so
that a later phase knows what each column meant. It trains nothing, loads nothing,
serves nothing and registers nothing, and a figure it could not compute is ``null``
rather than ``0`` — ``0`` would claim a repository with no commits when the truth is
that nobody has ever scanned it.

One honest limitation, stated here so it is not discovered later
----------------------------------------------------------------
:class:`~app.schemas.career.CareerEvidenceListRead` and
:class:`~app.schemas.career.CareerExperienceListRead` promise tallies that describe
*every* matching row rather than the page beside them, and
:class:`~app.repositories.career.CareerRepository` returns a page and a total with no
tallies attached. The service therefore reads the matching rows back through the
repository's own page method and counts them there, bounded by
``career_max_evidence`` for evidence and by a named constant for the timeline. The
alternative was to restate the repository's filter construction in the service, and
a second copy of a ``WHERE`` clause is a second answer that can silently disagree
with the list above it — which is the exact failure the list schemas warn about by
name. A set larger than the bound is under-counted rather than guessed at.
"""

from app.services.career.service import CareerIntelligenceService

__all__ = ["CareerIntelligenceService"]
