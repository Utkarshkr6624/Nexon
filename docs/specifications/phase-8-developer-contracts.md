# Phase 8 — Developer Intelligence: Frozen Contracts

> **Status: 🔴 frozen.** Every agent in the Phase 8 swarm codes against this file. If an
> implementation needs a change here, it stops and reports rather than inventing its own
> shape — two agents guessing the same name differently is the one failure mode this
> document exists to prevent.

---

## 0. Governing rules (from the brief, and not negotiable)

1. **Git data is evidence, not a verdict.** NEXUS reports what the repository records.
   It never claims working time, productivity, focus or hours — a commit timestamp
   cannot prove how long anyone worked. Factual language only:
   *"18 commits were recorded this week."* Never *"You were productive for 6 hours."*
2. **No hosted service.** Local paths and the system `git` CLI. No GitHub API, no tokens.
3. **A broken repository must never break NEXUS.** Every scan is wrapped; a failure is a
   row with a human message, never a stack trace and never a 500 that takes the page down.
4. **No arbitrary filesystem access.** A registered path is validated as a git work tree
   before it is stored, and the API exposes no endpoint that reads arbitrary paths.
5. **Server-side ownership, always.** Every query filters on `user_id`. Another account's
   repository is a 404, never a 403.
6. **No ML in Phase 8.** §8.8 is a feature *extractor*, not a trainer.

---

## 1. Enumerations — `app/models/enums.py`, appended block

Appended to the **existing** `ActivityEvent` StrEnum, after the Phase 7 block, under a
banner comment matching the existing style:

```python
    # -- Phase 8 (Developer Intelligence) -------------------------------------
    # Repository and commit facts are recorded here for the same reason the task
    # lifecycle is: they are the trail Phase 6 analytics reads and the surface
    # Phase 10 learns from. A scan writes one REPOSITORY_SCANNED row rather than
    # one row per commit, because a re-scan of an unchanged repository is not new
    # history — COMMIT_DETECTED is reserved for a commit the scan had not seen.
    REPOSITORY_REGISTERED = "repository_registered"
    REPOSITORY_UPDATED = "repository_updated"
    REPOSITORY_SCANNED = "repository_scanned"
    REPOSITORY_REMOVED = "repository_removed"
    COMMIT_DETECTED = "commit_detected"
    BRANCH_CREATED = "branch_created"
    BRANCH_CHANGED = "branch_changed"
    FILE_ACTIVITY_DETECTED = "file_activity_detected"
```

`validate_activity_event` already accepts any member; **no new `validate_*` helper is
needed**. Add all eight to `__all__` only if they are new module symbols — they are not,
`ActivityEvent` is already there.

A **new** `GitScanStatus` StrEnum is added to `app/models/enums.py`:

```python
class GitScanStatus(StrEnum):
    """The outcome of one attempt to read a repository from disk.

    ``PENDING`` is not a member: a scan is a synchronous request, so a row is only
    ever written once the attempt has already finished.
    """

    OK = "ok"
    ERROR = "error"
```

with `validate_git_scan_status(value: GitScanStatus | str) -> GitScanStatus` following the
existing helper template (docstring naming the concrete failure it prevents). Add both to
`__all__`.

---

## 2. Schema — `app/models/developer.py`, migration `0008`

Revision id `"0008"`, `down_revision = "0007"`, filename
`0008_phase8_developer_intelligence.py`, **no `app.models` import**, explicit DDL.

### 2.1 `git_repositories`

| column | type | notes |
|---|---|---|
| `id` | `UUID` PK | `UUIDPrimaryKeyMixin` |
| `user_id` | `UUID` FK `users.id` `ON DELETE CASCADE` | not null |
| `name` | `String(200)` | not null — user-facing label, defaults to the directory name |
| `local_path` | `Text` | not null — normalized absolute path |
| `description` | `Text` | nullable |
| `primary_language` | `String(64)` | nullable |
| `project_id` | `UUID` FK `projects.id` `ON DELETE SET NULL` | nullable — the trail outlives the project |
| `is_active` | `Boolean` server_default `true` | not null |
| `current_branch` | `String(255)` | nullable — null when HEAD is detached |
| `default_branch` | `String(255)` | nullable |
| `branch_count` | `Integer` server_default `"0"` | not null |
| `commit_count` | `Integer` server_default `"0"` | not null |
| `first_commit_at` | `DateTime(timezone=True)` | nullable — null for an empty repository |
| `latest_commit_at` | `DateTime(timezone=True)` | nullable |
| `working_tree_dirty` | `Boolean` server_default `false` | not null |
| `last_scanned_at` | `DateTime(timezone=True)` | nullable — null until the first scan |
| `last_scan_status` | `String(16)` server_default `'ok'` | nullable |
| `last_scan_error` | `Text` | nullable — a human sentence, never a traceback |
| `created_at` / `updated_at` | `DateTime(timezone=True)` server_default `now()` | `TimestampMixin` |

```python
__table_args__ = (
    UniqueConstraint("user_id", "local_path", name="uq_git_repositories_owner_path"),
    Index("ix_git_repositories_user_id", "user_id"),
    Index("ix_git_repositories_owner_active", "user_id", "is_active"),
    Index("ix_git_repositories_project_id", "project_id"),
)
```

One path may be registered **once per account**. Two accounts registering the same path
is allowed — it is a local directory and both may legitimately watch it.

### 2.2 `git_commits`

| column | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `user_id` | `UUID` FK `users.id` `ON DELETE CASCADE` | not null |
| `repository_id` | `UUID` FK `git_repositories.id` `ON DELETE CASCADE` | not null |
| `commit_hash` | `String(64)` | not null — full SHA |
| `short_hash` | `String(12)` | not null — for display only |
| `committed_at` | `DateTime(timezone=True)` | not null |
| `message` | `Text` | not null — subject line only |
| `author_name` | `String(200)` | nullable |
| `author_email` | `String(320)` | nullable |
| `additions` | `Integer` server_default `"0"` | not null |
| `deletions` | `Integer` server_default `"0"` | not null |
| `files_changed` | `Integer` server_default `"0"` | not null |
| `branch` | `String(255)` | nullable — *may* be null: a commit reachable from no branch, or a scan that did not resolve one |
| `created_at` | `DateTime(timezone=True)` server_default `now()` | no `updated_at` — an observed commit is immutable |

```python
__table_args__ = (
    UniqueConstraint("repository_id", "commit_hash", name="uq_git_commits_repo_hash"),
    Index("ix_git_commits_user_id", "user_id"),
    Index("ix_git_commits_repo_committed", "repository_id", "committed_at"),
    Index("ix_git_commits_user_committed", "user_id", "committed_at"),
)
```

The `(repository_id, commit_hash)` uniqueness is what makes re-scanning idempotent: a
re-scan upserts rather than duplicating, and a commit with no counted lines is stored as
`0` rather than dropped.

### 2.3 `git_branches`

| column | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `user_id` | `UUID` FK `users.id` `ON DELETE CASCADE` | not null |
| `repository_id` | `UUID` FK `git_repositories.id` `ON DELETE CASCADE` | not null |
| `name` | `String(255)` | not null |
| `is_current` | `Boolean` server_default `false` | not null |
| `is_default` | `Boolean` server_default `false` | not null |
| `head_commit_hash` | `String(64)` | nullable |
| `last_committed_at` | `DateTime(timezone=True)` | nullable |
| `created_at` / `updated_at` | | `TimestampMixin` |

```python
__table_args__ = (
    UniqueConstraint("repository_id", "name", name="uq_git_branches_repo_name"),
    Index("ix_git_branches_user_id", "user_id"),
    Index("ix_git_branches_repo_id", "repository_id"),
)
```

### 2.4 `git_scan_runs`

The append-only record of scan attempts. `UUIDPrimaryKeyMixin, Base` only — **no
`updated_at`**, because a scan run is a fact about a moment and an `onupdate` on it would
be a lie.

| column | type | notes |
|---|---|---|
| `id` | `UUID` PK | |
| `user_id` | `UUID` FK `users.id` `ON DELETE CASCADE` | not null |
| `repository_id` | `UUID` FK `git_repositories.id` `ON DELETE CASCADE` | not null |
| `status` | `String(16)` server_default `'ok'` | not null |
| `commits_discovered` | `Integer` server_default `"0"` | not null |
| `commits_added` | `Integer` server_default `"0"` | not null |
| `branches_discovered` | `Integer` server_default `"0"` | not null |
| `duration_ms` | `Integer` server_default `"0"` | not null |
| `error` | `Text` | nullable |
| `scanned_at` | `DateTime(timezone=True)` server_default `now()` | not null |
| `created_at` | `DateTime(timezone=True)` server_default `now()` | |

```python
__table_args__ = (
    Index("ix_git_scan_runs_user_id", "user_id"),
    Index("ix_git_scan_runs_repo_scanned", "repository_id", "scanned_at"),
)
```

### 2.5 Registration checklist

- `app/models/__init__.py` — `from app.models.developer import (...)`, alphabetical, and
  every symbol in the flat `__all__`.
- `tests/test_migration_ddl.py` — append `"migrations.versions.0008_phase8_developer_intelligence"`
  to `MIGRATION_MODULES`.

---

## 3. Git engine — `app/services/developer/git.py`

Uses the **system `git` CLI** through `asyncio.create_subprocess_exec` with an argument
list (never `shell=True`, never a string). GitPython is **not** a dependency and must not
be added.

### 3.1 Safety

```python
DEFAULT_GIT_TIMEOUT_SECONDS = 30
MAX_COMMITS_PER_SCAN = 2000
MAX_SCAN_OUTPUT_BYTES = 16 * 1024 * 1024
```

- `run_git(repo_path, *args, timeout=...)` → returns stdout, or raises `GitCommandError`.
- **`cwd=repo_path` always**, and every argument is passed as a separate element.
- Output is truncated at `MAX_SCAN_OUTPUT_BYTES`; exceeding it is a scan error, not a
  memory exhaustion.
- A timeout kills the process and raises `GitCommandError("The repository scan timed out.")`.
- Non-zero exit → `GitCommandError` carrying git's **stderr, sanitized**: strip absolute
  paths that contain the user's home directory, and never let a traceback through.

### 3.2 Path validation — `validate_repository_path(path) -> Path`

```python
def validate_repository_path(path: str | os.PathLike[str]) -> Path:
    """Resolve ``path`` and prove it is a git work tree.

    Raises:
        GitRepositoryError: If the path does not exist, is not a directory, or does
            not contain a ``.git`` entry.
    """
```

Order: expand `~` → `Path.resolve(strict=True)` → `is_dir()` → `(path / ".git").exists()`.
The resolved absolute path is what gets stored, so a relative path cannot later resolve
somewhere else. A bare `git init`ed directory with **no commits** is valid and must be
accepted — §5 tests exactly that.

### 3.3 The reader

```python
@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    path: Path
    name: str
    current_branch: str | None       # None on a detached HEAD — not an error
    default_branch: str | None
    branches: tuple[BranchInfo, ...]
    commits: tuple[CommitInfo, ...]
    tracked_file_count: int
    language_distribution: tuple[tuple[str, int], ...]   # (language, file count), desc
    primary_language: str | None
    working_tree_dirty: bool
    working_tree_changes: int
    first_commit_at: datetime | None
    latest_commit_at: datetime | None
```

`read_repository(path, *, since: datetime | None = None, limit: int = MAX_COMMITS_PER_SCAN)`
→ `RepositorySnapshot`, raising `GitRepositoryError` for anything unreadable.

Git invocations, one per concern (do not build a giant porcelain dump):

| need | command |
|---|---|
| current branch | `rev-parse --abbrev-ref HEAD` — returns `HEAD` when detached |
| default branch | `symbolic-ref --quiet refs/remotes/origin/HEAD`, then `HEAD` |
| branches | `for-each-ref --format=%(refname:short)%09%(objectname)%09%(committerdate:iso-strict) refs/heads` |
| commits | `log --no-merges -n <limit> --since=<iso> --date=iso-strict --pretty=format:%H%x09%h%x09%aI%x09%an%x09%ae%x09%s` |
| numstat | `log --no-merges -n <limit> --since=<iso> --numstat --pretty=format:%x00%H` |
| shortstat per commit | same `%x00%H` delimiter, `--shortstat` |
| dirty tree | `status --porcelain` (count non-empty lines) |
| tracked files | `ls-files` |
| languages | `ls-files` + a static extension→language map |

**Branch attribution is explicitly best-effort.** A commit's `branch` is the branch whose
head currently *contains* it (`git branch --contains` is too slow per commit — resolve it
once with a single `for-each-ref` pass over the collected hashes). When no branch can be
resolved, `branch` is `None`. Never guess.

Language distribution counts **files by extension** against a static, in-repo map
(`py→Python`, `ts`/`tsx`→TypeScript`, `js`/`jsx`→JavaScript`, `java`→Java`, `sql`→SQL`,
`go`→Go`, `rs`→Rust`, `rb`→Ruby`, `c`/`h`→C`, `cpp`/`hpp`→C++`, `cs`→C#`, `php`→PHP`,
`swift`→Swift`, `kt`→Kotlin`, `html`→HTML`, `css`→CSS`, `sh`→Shell`, `md`→Markdown`,
`json`→JSON`, `yml`/`yaml`→YAML`). Unknown extensions are **not** bucketed into a
"Other" bucket that could then be called a language; they are simply not counted.
`primary_language` is the most common, or `None`.

### 3.4 Incremental scanning

`read_repository(..., since=...)` passes `--since` so a re-scan transfers only new
commits. The service passes `since = repository.latest_commit_at` when it is set. A
repository with no `latest_commit_at` is scanned in full.

---

## 4. Pure metrics — `app/services/developer/metrics.py`

Pure functions only. No DB, no clock, no subprocess. Every function returns a
`DeveloperMetric`, never a bare number:

```python
@dataclass(frozen=True, slots=True)
class DeveloperMetric:
    key: str
    label: str
    value: float
    unit: str                    # "count" | "lines" | "days" | "ratio" | "score"
    definition: str              # how it is computed, in one sentence
    window_days: int | None      # None = whole history
    source: str                  # which recorded facts it read
    explanation: str             # the sentence shown to the user, with the figures in it
    available: bool = True
    reason_if_unavailable: str | None = None
```

`NOT_ENOUGH_DATA = "Not enough data to assess this yet"` — a single module-level string,
matching `services/risk/scoring.py`. **Absence of measurement is `available=False` with a
reason; a real measurement of zero is `value=0, available=True`.** The two are different
answers and must never be conflated.

The eight required metrics, each with its stated definition:

| key | computes |
|---|---|
| `commit_activity` | commits in the window |
| `repository_activity` | distinct repositories with ≥1 commit in the window |
| `change_volume` | additions + deletions in the window |
| `active_days` | distinct UTC dates carrying ≥1 commit in the window |
| `consistency` | `active_days / window_days`, as a 0–1 ratio |
| `repository_growth` | commits landing on branches first seen in the window |
| `maintenance_activity` | commits touching files not modified in the 90 days before the window |
| `recent_momentum` | commits in the last 7 days ÷ commits in the preceding 7 days; `None` (→ `available=False`) when the denominator is 0 |

**Every explanation must contain the figures it is built from**, in the style of
`RecommendationDraft.__post_init__`, which rejects a reason with no digit in it. Apply the
same rule here — a metric whose explanation has no number in it is a bug.

**Never** emit hours, focus, productivity, effort or "time spent". Commit counts, active
days and changed lines only.

The activity series builder returns one bucket per day/week/month across the range, with
**zero-filled gaps** — a chart that skips a quiet Tuesday is lying about the week.

---

## 5. Configuration — `app/core/config.py`

Appended, all with `#:` comments, all prefixed `developer_`:

```
developer_git_timeout_seconds: int = 30
developer_max_commits_per_scan: int = 2000
developer_max_repositories: int = 100
developer_default_window_days: int = 30
developer_max_window_days: int = 366
developer_activity_granularity_default: str = "day"     # day | week | month
developer_path_allowlist: str = ""                       # comma-separated; empty = any readable absolute path
```

When `developer_path_allowlist` is non-empty, `validate_repository_path` rejects any path
that is not under one of the listed roots.

---

## 6. HTTP API — `app/api/v1/developer.py`

`router = APIRouter(prefix="/developer", tags=["developer"])`, mounted in
`app/api/v1/router.py` as a plain `include_router(developer.router)`.

Reuse `Permission.ANALYTICS_READ` — the Phase 7 precedent. **No new `Permission` member**,
so `tests/test_permissions.py` needs no change.

**Literal sub-paths are declared before `/{repository_id}`.** Starlette matches in
declaration order; `/developer/summary` must not be swallowed by `/developer/{repository_id}`.

| method | path | purpose |
|---|---|---|
| `GET` | `/developer/summary` | dashboard overview + activity series |
| `GET` | `/developer/metrics` | the eight metrics, each fully explained |
| `GET` | `/developer/activity` | commits bucketed day/week/month, optionally per repository |
| `GET` | `/developer/commits` | global commit timeline across repositories |
| `GET` | `/developer/features` | ML-ready feature vector (§8) |
| `GET` | `/developer/repositories` | list, paginated |
| `POST` | `/developer/repositories` | register after validating the path |
| `GET` | `/developer/repositories/{repository_id}` | detail |
| `PATCH` | `/developer/repositories/{repository_id}` | edit metadata only — **never the path** |
| `DELETE` | `/developer/repositories/{repository_id}` | remove, cascading its commits/branches/scans |
| `POST` | `/developer/repositories/{repository_id}/scan` | rescan synchronously |
| `GET` | `/developer/repositories/{repository_id}/commits` | that repository's history |
| `GET` | `/developer/repositories/{repository_id}/branches` | branch list |
| `GET` | `/developer/projects/{project_id}` | §5 project integration |

Every route takes `AuthenticatedUser`, filters on `current_user.id`, and returns 404 — never
403 — for another account's rows. `limit`/`offset` are bounded and `?limit=500` is a 422.

Registration additionally caps at `developer_max_repositories` and emits `REPOSITORY_REGISTERED`.

**Scanning is a synchronous `POST` returning 200**, exactly like `POST /intelligence/evaluate`.
There is no background scheduler in NEXUS and Phase 8 does not add one.

---

## 7. ML-ready features — `GET /developer/features`

A **feature extractor**, not a model. Returns named numbers with a schema version so a
later trainer knows what the columns meant:

```json
{
  "schema_version": "developer_features.v1",
  "generated_at": "2026-07-01T00:00:00Z",
  "window_days": 30,
  "features": { "commits_last_7d": 18, "commits_last_30d": 61, "active_days_7d": 5,
                "active_days_30d": 17, "files_changed_7d": 42, "additions_7d": 1180,
                "deletions_7d": 640, "repository_age_days": 412, "inactivity_days": 0,
                "commit_frequency": 2.03, "project_association": true },
  "repositories": [ { "repository_id": "...", "...": "same feature names" } ]
}
```

`repository_age_days` and `inactivity_days` are `null` for a repository with no commits —
null, **not** 0, because 0 would claim "committed today".

No model, no training, no inference, no registry. Phase 10 does that.

---

## 8. Frontend contract

### `src/types/developer.ts`
Mirrors the wire exactly. Two written rules, both enforced by review:
1. the backend emits `null`, never an absent key — every nullable is `T | null` and required;
2. a figure that could not be computed is `null`, never `0`.

Re-export everything from `src/types/index.ts`.

### `src/services/developer.ts`
`DEVELOPER_ENDPOINTS` as an `as const` map (functions for id-bearing paths), the local
`queryFrom` helper, and one typed `(params = {}, signal?: AbortSignal)` function per
endpoint.

### `src/features/developer/`
`hooks.ts` (`developerKeys` rooted at `['developer']`, `placeholderData: (previous) =>
previous` on window reads, per-endpoint `enabled` gating), `format.ts`, and
`components/` reusing `ChartShell`, `ChartTooltip`, `TrendChart`, `AnalyticsBarChart`,
`Heatmap`, `MetricCard`, `EmptyAnalytics` and `LazyChart` from `features/analytics` rather
than re-implementing them.

Empty copy must explain **why it is empty and what fills it**. Insufficient data reads
**"Not enough data yet."** Never fabricate a value, and never `?? 0` a nullable — use the
analytics formatters, which return `—`.

### Pages
`src/pages/developer-page.tsx` **replaces** the `ModulePage` stub (route and sidebar entry
already exist). A repository detail page is added at `/developer/:repositoryId`, following
the `/projects/:projectId` pattern, with the lazy entry added to `src/routes/lazy-pages.ts`
and the route inside the `AppLayout` children.

Do **not** touch `sidebar.tsx` or `command-palette.tsx` — they render from
`features/modules/catalog.ts`, which already registers `/developer`. Update that module's
`summary`/`vision`/`phase` and its `capabilities`/`metrics` copy to describe what shipped.

---

## 9. Phase 10 boundary

Phase 8 produces features. It does not train, load, serve or register a model. No
Kaggle, no MLflow, no inference endpoint, no Ollama.
