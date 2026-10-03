"""Phase 8 — developer intelligence from local git repositories.

Three modules, one direction of flow:

* :mod:`app.services.developer.git` — reads a directory through the system
  ``git`` CLI. No database, no ORM, no configuration.
* :mod:`app.services.developer.metrics` — pure functions from recorded rows to
  the eight explained metrics and the activity series. No clock, no database,
  no subprocess.
* :mod:`app.services.developer.service` — reads and writes Phase 8 storage, calls
  the other two, assembles the wire shapes, and emits the history events.

The split is the same one :mod:`app.services.risk` makes, and for the same
reason. A metric computed from ``(commits=18, active days=5, window=30)`` returns
the same number on any machine and can be asserted to the exact value in a test
that provisions no database and reads no git repository. The orchestration above
it is asserted separately against a seeded fixture. Neither test needs the other,
and a rule that cannot be tested without a live PostgreSQL instance and a real
``.git`` directory is a rule that stops being checked the moment the environment
is awkward — which is the moment it matters.

Why the boundary is drawn *there*
---------------------------------
The tempting alternative is a single ``DeveloperIntelligenceService`` that also
computes the metrics, so there is one file and one import. It fails in a specific
way: the moment anything about the arithmetic changes, the change has to be
re-asserted through a database and a subprocess, so it is not, so the arithmetic
drifts. And it fails in a second way, which is worse — a service that can compute
a metric can also invent one. Here the metrics module is the *only* thing that
produces a :class:`~app.services.developer.metrics.DeveloperMetric`, and that
class refuses to be constructed with an explanation carrying no figure or one
reaching for "productive", "focus", "hours" or "effort". The rule that a commit
timestamp is evidence rather than a verdict is therefore a property of the code
that would have to be deliberately edited to break, not an intention in a docstring
somewhere above it.

Two more reasons the split earns its keep, both about failure:

**A broken repository must never break NEXUS.** :mod:`git` converts every way a
subprocess can misbehave into one of two exception types carrying a human
sentence, and :class:`DeveloperIntelligenceService` turns one of those into a
``GitScanRun`` row with a status and a message. The containment is in the service
because only the service knows what "a row instead of an exception" means — the
git engine has no idea a database exists.

**Absence of measurement is not zero.** :mod:`metrics` decides what can be
declared unavailable and what is a real zero, because only it knows the difference
between a window with no commits and a window nobody measured. The service's job
is to pass that distinction through untouched: it maps an unavailable metric's
``value`` to null and leaves a measured zero alone.
"""

from app.services.developer.git import (
    GitCommandError,
    GitError,
    GitRepositoryError,
    RepositorySnapshot,
    read_repository,
    validate_repository_path,
)
from app.services.developer.metrics import (
    DEVELOPER_METRICS,
    NOT_ENOUGH_DATA,
    ActivityGranularity,
    ActivitySeries,
    DeveloperMetric,
    MetricKey,
    MetricSample,
    activity_series,
    build_metrics,
)
from app.services.developer.service import DeveloperIntelligenceService

__all__ = [
    "DEVELOPER_METRICS",
    "NOT_ENOUGH_DATA",
    "ActivityGranularity",
    "ActivitySeries",
    "DeveloperIntelligenceService",
    "DeveloperMetric",
    "GitCommandError",
    "GitError",
    "GitRepositoryError",
    "MetricKey",
    "MetricSample",
    "RepositorySnapshot",
    "activity_series",
    "build_metrics",
    "read_repository",
    "validate_repository_path",
]
