# Phase 8 — Final Report: Developer Intelligence

Companion to [`phase-8-9-developer-learning-career.md`](./phase-8-9-developer-learning-career.md),
which is the brief, and to [`phase-8-developer-contracts.md`](./phase-8-developer-contracts.md),
the frozen internal contract this phase was built against in parallel. Where this report and
the contract disagree, this report is the record of what shipped.

Phase 9's report is [`phase-9-learning-career-report.md`](./phase-9-learning-career-report.md).
Both phases ran concurrently against the same tree, and §9 below records the two
cross-phase interactions that came out of that.

---

## 1. What was delivered

NEXUS reads **local git repositories** through the `git` CLI, on the machine the backend
runs on, and reports what git recorded — commits, branches, changed lines, file activity —
as counts, each carrying the arithmetic it was built from. There is no hosted account to
connect, no network call, and no model.

| Surface | What exists |
| --- | --- |
| Git engine | `app/services/developer/git.py` — an asyncio `git` CLI wrapper. No `shell=True`, no GitPython, no network, a wall-clock timeout with a kill, an output byte ceiling, and a stderr sanitiser |
| Pure metrics | `app/services/developer/metrics.py` — eight metrics, zero I/O, no database, no clock, no subprocess. Assertable without PostgreSQL and without a `.git` directory |
| Service | `app/services/developer/service.py` — owner-scoped predicates, validation-before-write, idempotent upserts, event emission |
| Persistence | `app/repositories/developer.py` + migration `0008` — four tables |
| HTTP | `app/api/v1/developer.py` — 14 routes, all reusing `Permission.ANALYTICS_READ` |
| Frontend | `/developer` and a `/developer/repositories/:id` detail page, `features/developer/**`, `types/developer.ts`, `services/developer.ts` |
| ML-ready | `GET /developer/features` → `developer_features.v1` |

### The governing rule, and how it is enforced rather than intended

**A commit timestamp is evidence, never a verdict.** Git records that a commit object
carries an author date; it does not record how long anyone worked. So there is no `hours`,
`minutes_spent`, `effort` or `focus` column anywhere in the schema, and no table here
could be summed into one. The counts are line changes git counted from diffs, and the
number of commits — changes and events, never time.

The rule is a property of the code, not of whoever writes the next sentence:
`app/services/developer/metrics.py` refuses to build an explanation containing
`rest`, `fatigue`, `burnout`, `tired`, `focus`, `productive`, `productivity` or `effort`
(a denied-word list asserted in the module and repeated in the tests), and every one of the
eight metrics carries its definition, its unit as *data* rather than decoration, and its
figures.

**A broken repository must never break NEXUS.** Every scan is wrapped, so a deleted
directory, a corrupt `.git`, an unreadable network share and a git process that hangs past
its timeout all come back as a `git_scan_runs` row with `status='error'` and a human
sentence — never an exception. `POST /developer/repositories/{id}/scan` therefore answers
**200 whether the read worked or not**. There is no code path on that router where a bad
directory produces a 500.

---

## 2. Database changes

`migrations/versions/0008_phase8_developer_intelligence.py` — `revision="0008"`,
`down_revision="0007"`, explicit DDL, **no `app.models` import**, as in `0001`–`0007`, so a
later change to `app/models/` cannot rewrite history. Creates four tables, ten indexes, four
unique/check constraints.

### `git_repositories`

The registered work tree and the counters from its **last** scan — not from everything that
ever existed.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` PK | `UUIDPrimaryKeyMixin` |
| `user_id` | `UUID` FK `users.id` **CASCADE** | not null — a row nobody can reach is a row nothing can read |
| `name` | `String(200)` not null | a user-facing label, renamed freely; separate from the path on purpose |
| `local_path` | `Text` not null | the **resolved absolute** path, so a relative path cannot later resolve elsewhere under a different working directory. Text, not a bounded `String`: a deep POSIX path outgrows a reasonable limit |
| `description` | `Text` nullable | |
| `primary_language` | `String(64)` **nullable, no placeholder** | "Python" and "no tracked file we recognise" are different answers; `""` would be a third |
| `project_id` | `UUID` FK `projects.id` **SET NULL** | the observed history outlives the project it was linked to |
| `is_active` | `Boolean` default `true` | |
| `current_branch`, `default_branch` | `String(255)` nullable | nullable because a detached HEAD is a normal state and git reports it as the literal string `HEAD`, which must not be stored as though it were a branch name |
| `branch_count`, `commit_count` | `Integer` default `0` | counts of what the last scan saw |
| `first_commit_at`, `latest_commit_at` | `DateTime(tz)` nullable | null for a repository with no commits at all — a fresh `git init` is a valid registration, and `0` would claim a zero-length history rather than the absence of one |
| `working_tree_dirty` | `Boolean` default `false` | |
| `last_scanned_at` | `DateTime(tz)` nullable | |
| `last_scan_status` | `String(16)` default `'ok'` nullable | |
| `last_scan_error` | `Text` nullable | a human sentence, never a traceback and never an absolute path from inside the user's home directory |
| `created_at` / `updated_at` | | `TimestampMixin` — revised, so the pair is present |

**Constraints.** `uq_git_repositories_owner_path` — one registration per (account, path), so
the same directory cannot end up with two rows whose commit counts disagree. Deliberately
**not global**: the path is a local directory on the developer's own machine and two
accounts may each legitimately watch it. `ck_git_repositories_counts_non_negative` — a
negative line count is not a thin measurement but a broken one; a zero is legitimate and
stored (a binary file has no countable lines), so the constraint refuses only the value
that cannot mean anything and cannot reject a legitimate scan.

**Indexes.** `ix_git_repositories_user_id` (the account probe every read starts with);
`ix_git_repositories_owner_active` on `(user_id, is_active)` — the bare `user_id` index
cannot serve "my active repositories" because it carries no predicate on `is_active`;
`ix_git_repositories_project_id` — a nullable FK that is not the leading column of any
other index here.

### `git_commits`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `UUID` PK | |
| `user_id` | `UUID` FK `users.id` **CASCADE** | |
| `repository_id` | `UUID` FK `git_repositories.id` **CASCADE** | a commit whose repository no longer exists is not history of anything |
| `commit_hash` | `String(64)` not null | 64, not 40: `git rev-parse` returns a full SHA-256 object name on a repository using the newer object format, and a 40-wide column truncates it |
| `short_hash` | `String(12)` not null | display abbreviation, carried so the width is this schema's choice rather than whatever `%h` emitted |
| `committed_at` | `DateTime(tz)` not null | the author date git recorded, in UTC. Never a measure of time spent |
| `message` | `Text` not null | **subject line only**. The body is not stored: messages carry ticket ids and the occasional pasted credential, and none of it is needed to answer "how much changed" |
| `author_name` | `String(200)` nullable | |
| `author_email` | `String(320)` nullable | |
| `additions`, `deletions`, `files_changed` | `Integer` default `0` not null | zero is a real answer and is stored, so the commit count and the file count always describe the same set of commits |
| `branch` | `String(255)` **nullable** | best-effort attribution from the branch head that currently contains this commit; null when no branch does. Never a guess |
| `created_at` | `DateTime(tz)` default `now()` | **no `updated_at`** — an observed commit is immutable, and the mixin would stamp a revision git cannot make |

**Constraints.** `uq_git_commits_repo_hash` is the idempotency anchor: a re-scan of an
unchanged repository returns the same hashes, and without this the dashboard's commit
counts would double every time the user pressed the scan button. It is a **full** unique
constraint rather than Phase 7's partial one because a commit is not an episode — the same
commit is the same commit forever and there is no "resolved" version of one.
`ck_git_commits_counts_non_negative` for the reason above.

**Indexes.** `ix_git_commits_user_id`; `ix_git_commits_repo_committed` on
`(repository_id, committed_at)` for one repository's timeline; `ix_git_commits_user_committed`
on `(user_id, committed_at)` for the account-wide timeline and every metric not scoped to one
repository — a separate index because the leading column differs, so the composite above
cannot serve a `user_id` probe.

### `git_branches`

`id`, `user_id` (CASCADE), `repository_id` (CASCADE), `name String(255)`, `is_current`,
`is_default`, `head_commit_hash String(64)` nullable, `last_committed_at` nullable, plus the
`created_at`/`updated_at` pair — a branch *is* revised, its head moves. `uq_git_branches_repo_name`
is the same idempotency anchor as the commit hash. Indexes: `ix_git_branches_user_id`,
`ix_git_branches_repo_id` (which also serves the CASCADE).

### `git_scan_runs`

`id`, `user_id` (CASCADE), `repository_id` (CASCADE), `status String(16)` default `'ok'`,
`commits_discovered`, `commits_added`, `branches_discovered`, `duration_ms`,
`error Text` nullable, `scanned_at`, `created_at`.

**No `pending` member.** A scan is a synchronous request, so a row is only written once the
attempt has already finished; a row claiming to be in progress could outlive the request
that started it. **Failed attempts get rows too** — that is what lets a repository whose
directory has since been moved still explain itself. `duration_ms` is the wall-clock cost of
the CLI call and is explicitly *not* a measure of anybody's work.
`ck_git_scan_runs_counts_non_negative` again. Indexes `ix_git_scan_runs_user_id` and
`ix_git_scan_runs_repo_scanned` on `(repository_id, scanned_at)`.

### What this migration pointedly does not create

- **No `git_commit_files` table.** `git log --numstat` reports per-file counts and this
  schema stores only their sum. A per-file row table would grow to millions of rows for a
  mature codebase to answer questions Phase 8 does not ask. This is the direct cause of a
  real limitation — see §10.
- **No blame, no per-line churn.** The most reliable way to turn recorded facts into
  judgements about individual people.
- **No remote-tracking branches.** Only `refs/heads` is recorded; a `refs/remotes/*` mirror
  is somebody else's history and would double-count.
- **No `metadata` JSONB.** Phase 7's risk tables carry one because a detection run's raw
  inputs cannot be re-derived. Git inputs can be — the repository is still on disk and the
  next scan reads it again — so a blob would be a second copy that drifts from the first.

---

## 3. API changes

Fifteen routes, all requiring authentication (401) and `analytics.read` (403). A foreign id
is **404, not 403**. Verified against the live OpenAPI schema:

```
GET    /api/v1/developer/summary                            200  DeveloperSummaryRead
GET    /api/v1/developer/metrics                            200  DeveloperMetricRead[8]
GET    /api/v1/developer/activity                           200  DeveloperActivityRead
GET    /api/v1/developer/commits                            200  CommitListRead
GET    /api/v1/developer/features                           200  DeveloperFeatureVectorRead
GET    /api/v1/developer/repositories                       200  RepositoryListRead
POST   /api/v1/developer/repositories                       201  RepositoryRead
GET    /api/v1/developer/repositories/{repository_id}       200  RepositoryRead
PATCH  /api/v1/developer/repositories/{repository_id}       200  RepositoryRead
DELETE /api/v1/developer/repositories/{repository_id}       204  (no body)
POST   /api/v1/developer/repositories/{repository_id}/scan  200  ScanRunRead
GET    /api/v1/developer/repositories/{repository_id}/commits   200  CommitListRead
GET    /api/v1/developer/repositories/{repository_id}/branches 200  BranchListRead
GET    /api/v1/developer/projects/{project_id}              200  ProjectDeveloperRead
```

| Route | Purpose |
| --- | --- |
| `/summary` | Headline counts over a window, plus `window_days` so any sentence a client writes can name the range. `has_data: false` on an empty account — the flag that tells a client to explain an absence instead of rendering zeroes as a finding |
| `/metrics` | All eight metrics, **always eight elements**. One the data cannot support comes back `available: false`, `value: null` and a reason rather than being dropped; a *measured* zero keeps both its value and `available: true` |
| `/activity` | The commit series, **dense** — a quiet Tuesday arrives carrying `commits: 0` rather than being skipped, because a series that omits empty buckets compresses the timeline and makes a sparse fortnight read as dense as a busy one |
| `/commits` | Account-wide commit timeline, newest first, every filter narrowing `total` as well as `items`. A commit with no resolvable branch is **absent** from a branch filter rather than misfiled into it |
| `/features` | `developer_features.v1`. See §5 |
| `/repositories` (GET) | Registered repositories with the active/inactive split counted across every matching row, not the page |
| `/repositories` (POST) | Validate the path, prove it is a git work tree, prove it is under the allowlist **if one is configured**, then store the *resolved absolute* path. A bare `git init` with no commits registers successfully — it is the first thing a user does with this feature |
| `/repositories/{id}` (GET/PATCH/DELETE) | `PATCH` never moves `local_path` (the row's identity, and the one field checked against the filesystem) and never writes `primary_language` (measured by the scan, not typed by a person). `DELETE` cascades to commits, branches and scan runs: a row keeping its history while claiming the repository no longer exists would leave the account's metrics reading commits from a work tree the user removed |
| `/repositories/{id}/scan` (POST) | Rescan synchronously, `?full=false` by default. Incremental and idempotent: `--since` the stored `latest_commit_at`, upsert on `(repository_id, commit_hash)`. A rescan of an unchanged repository reports `commits_discovered` equal to what git returned and `commits_added` of 0 — that gap is the proof the upsert worked |
| `/repositories/{id}/commits`, `/branches` | The per-repository timeline and the branches under `refs/heads`, current first |
| `/projects/{project_id}` | One project's developer view, with the repositories carried alongside the counts so a project page cannot show a total that disagrees with the rows printed beneath it. `active_days` is counted across the project rather than summed per repository — one day with commits in three repositories is one active day |

### Conventions this phase established

Four rules that are properties of the code and were not previously recorded:

1. **Literal sub-paths are declared before every parameterised route.** Starlette matches in
   registration order and does not prefer a literal segment over a parameter, so
   `/developer/repositories/{repository_id}` registered above `/summary` would bind the
   literal string `summary` to the path parameter, fail its uuid conversion, and answer 422
   about an id that never existed — while the dashboard tile quietly lost its data. There is
   no `Path` annotation that fixes it; the order is the entire mechanism.
2. **A page-size cap is a rejection, not a silent truncation.** `?limit=500` is a 422,
   because a caller that asked for 500 and received 200 cannot tell a truncated page from a
   page that was always 200 rows long. The ceiling (200) is the same number the repository
   clamps to.
3. **Every `PATCH` applies its body with `exclude_unset=True`.** `None` means "write SQL
   NULL" downstream, so dumping the whole model would clear the description and the project
   link of any caller who only meant to rename something. An explicit `null` is honoured.
4. **A window ceiling is owned by the service, not declared on the route.** Only the lower
   bound (`ge=1`) is declared; a constant in the router would answer 422 against a limit the
   deployment has raised.

---

## 4. Frontend changes

Phase 8 replaced two `ModulePage` stubs. `/developer` and `/career` already existed as
routes, sidebar entries and command-palette items, so **no nav entry was added and
`sidebar.tsx` and `command-palette.tsx` were not touched.**

| File | Lines | Role |
| --- | --- | --- |
| `src/pages/developer-page.tsx` | 1114 | The dashboard: summary tiles, the eight metric cards, the activity chart, the language breakdown, the commit timeline, the scan-status panel, repository registration and scanning |
| `src/pages/developer-repository-page.tsx` | 672 | One repository: its branches, its commits, its scan history |
| `src/features/developer/components/*` | 14 files | `activity-chart-section`, `branch-list`, `commit-timeline`, `developer-badges`, `developer-empty-state`, `developer-format`, `developer-metric-card`, `developer-summary-tiles`, `developer-vocabulary`, `language-breakdown`, `repository-card`, `scan-status-panel` |
| `src/features/developer/hooks.ts`, `format.ts` | — | Query/mutation hooks and formatters |
| `src/types/developer.ts`, `src/services/developer.ts` | — | Wire types and one exported function per endpoint |

Test files added: `src/pages/developer-page.test.tsx`,
`src/pages/developer-repository-page.test.tsx`,
`src/features/developer/components/developer-components.test.tsx`.

**Frontend conventions, mirroring the Phase 7 structure.** Every nullable is `T | null` and
required; a figure that could not be computed is `null`, never `0`; every formatter takes
`number | null` and returns `—`; empty copy explains why it is empty; insufficient data
reads **"Not enough data yet"**; charts go through `LazyChart` and reuse `ChartShell`,
`ChartTooltip`, `TrendChart`, `AnalyticsBarChart`, `MetricCard` and `ScoreCard` from
`features/analytics` rather than re-implementing them. `noUncheckedIndexedAccess` and
`verbatimModuleSyntax` are on.

The `/developer` entry in `features/modules/catalog.ts` was updated to describe what shipped.
`to`, `label`, `icon` and `keywords` are unchanged.

---

## 5. ML-ready data structures

`GET /developer/features` returns named numbers under a **closed** schema version.

**`developer_features.v1`** (`app/schemas/developer.py`, `FEATURE_SCHEMA_VERSION`):

| Column | Type | Meaning |
| --- | --- | --- |
| `commits_last_7d` | `int` | commit objects recorded in the last 7 days |
| `commits_last_30d` | `int` | same, 30 days |
| `active_days_7d` | `int` | distinct UTC dates carrying at least one commit |
| `active_days_30d` | `int` | same, 30 days |
| `files_changed_7d` | `int` | files touched across those commits |
| `additions_7d` | `int` | lines added |
| `deletions_7d` | `int` | lines removed |
| `repository_age_days` | `int \| null` | null when the repository has no commits — never `0`, which would assert it was created today |
| `inactivity_days` | `int \| null` | null when no commit has ever been recorded; never `0` for "unknown" |
| `commit_frequency` | `float` | commits per day over the window |
| `project_association` | `bool` | whether the repository is linked to a project |

The envelope is `{schema_version, generated_at, window_days, features, repositories}`,
where `repositories` is **one row per registered repository** carrying the same
`DeveloperFeatureValues` plus its `repository_id`. `generated_at` comes from the **database**
clock rather than the host's, so it belongs on the same timeline as the rows it reads.

**A figure that could not be computed is `null`, never `0`.** Inside a training matrix a
fabricated zero is indistinguishable from an observed one once it reaches a trainer.

**An extractor, not a model.** Nothing is trained, loaded, served or inferred, and no
registry exists. Phase 10 is that work.

---

## 6. Tests

### Collected

256 backend tests across the seven Phase 8 files, obtained with
`pytest --collect-only -q` (real output, per file):

| File | Tests |
| --- | --- |
| `test_developer_git.py` | 66 |
| `test_developer_api.py` | 60 |
| `test_developer_metrics.py` | 45 |
| `test_developer_repository.py` | 36 |
| `test_developer_schema.py` | 26 |
| `test_developer_service.py` | 16 |
| `test_developer_git_integration.py` | 7 |
| **Total** | **256** |

Frontend: three test files (`developer-page.test.tsx`,
`developer-repository-page.test.tsx`, `developer-components.test.tsx`).

### Actually executed

These were run by the documentation agent on this machine. Real output, not a projection:

```text
$ cd backend && .venv/Scripts/python.exe -m pytest tests/test_developer_api.py \
      tests/test_developer_git_integration.py -q -p no:warnings
...................................................................      [100%]
67 passed in 68.73s (0:01:08)
```

```text
$ cd backend && .venv/Scripts/python.exe -m pytest tests/test_migration_ddl.py \
      tests/test_migrations.py -q
........................................................................ [ 46%]
........................................................................ [ 92%]
...........                                                              [100%]
155 passed in 0.55s
```

The 155 migration tests render `0001`–`0009` offline and compare every emitted
`CREATE TABLE` column, foreign key, constraint and index against `Base.metadata`. That is
real evidence that the DDL `0008` emits and the DDL the models describe are the same
schema.

```text
$ cd backend && .venv/Scripts/python.exe -m ruff check app/
All checks passed!
```

### Not executed by the documentation agent

Stated plainly rather than implied:

- **The full backend suite was not run.** Other agents were working concurrently against
  the same database, and `tests/conftest.py` enforces a PostgreSQL advisory lock per test
  database precisely so two sessions cannot destroy each other's rows.
- **`test_developer_git.py` (66), `test_developer_metrics.py` (45),
  `test_developer_repository.py` (36), `test_developer_schema.py` (26) and
  `test_developer_service.py` (16) were collected but not executed here.** They are
  reported as collected, not as passing.
- **No frontend command was run for this report's own files**, because the documentation
  agent owns no frontend file. `npx tsc -b --force` *was* run across the whole workspace
  and its output is recorded verbatim in the Phase 9 report §7. Every error it reported was
  in another agent's **test** file; **no error was reported in any Phase 8 file**.

---

## 7. Bugs found and fixed

Every one of these was found by a Phase 8 test or by review of code Phase 8 wrote.

### The two defects three separate agents independently reported

Both were real, both shipped broken at one point during this phase, and both are fixed in
the tree. They are recorded here because three reviewers found each independently, which
is itself evidence that neither was covered by a test at the time.

1. **An unhandled `GitRepositoryError` escaped as a 500 instead of the documented 422.**
   `DeveloperIntelligenceService.register_repository` promises `ValidationError` (422) for
   a path that is not a readable git work tree, and `validate_repository_path` raises
   `GitRepositoryError` for exactly that. `GitRepositoryError` is a bare `Exception`
   subclass with no registered handler, so it reached the client as an unhandled 500 — the
   opposite of what the method documents, and a direct violation of the phase's own rule
   that a bad repository never breaks NEXUS. Fixed by translating it at the boundary
   (`backend/app/services/developer/service.py:307-319`):

   ```python
   try:
       resolved = validate_repository_path(local_path, allowlist=...)
   except GitRepositoryError as error:
       raise ValidationError(str(error)) from error
   ```

   The engine's own human sentence travels with it, so no row is ever created that fails on
   every future scan.

2. **`COMMIT_DETECTED` was never emitted, because the high-water mark was read *after* the
   identity-mapped write.** `_record_scan_findings` emits `COMMIT_DETECTED` only for
   commits strictly after the stored `latest_commit_at`. But `update_scan_state` is an
   `UPDATE ... RETURNING GitRepository` with `populate_existing=True` run against the same
   identity-mapped instance the caller was handed, so by the time the mark was read,
   `repository.latest_commit_at` already held **this scan's own** newest timestamp.
   `committed_at > high_water_mark` could then never hold, and the event silently never
   fired — for any scan, ever, including a first scan of a mature repository. Nothing about
   the surface would have shown it: the counts were correct, the timeline was correct, and
   only the Phase 10 training feed was quietly empty. Fixed by reading the mark **before**
   any write (`backend/app/services/developer/service.py:671-678`), and by threading it
   into `_record_scan_findings` as an explicit parameter rather than re-reading the object.

### The rest

3. **The commit count would have fallen on every rescan.** `commit_count` was being
   assigned what the scan returned. An incremental scan returns only the commits *after*
   the high-water mark, so pressing the button on an active repository would have made its
   commit count go **down**. It now accumulates `repository.commit_count + inserted`, and
   the pre-write value is captured before the write that would overwrite it.

4. **`GitScanStatus.PENDING` did not exist but the type implied it could.** A scan is
   synchronous, so a `pending` row could outlive the request that started it. The enum has
   two members and the migration's column comment says why.

5. **The Phase 8 activity feed would have claimed a "created" branch event for a branch
   that had existed for two years.** `BRANCH_CREATED` now requires that the pre-write
   branch listing was *complete*; a repository with more branches than one page skips
   branch events entirely rather than emitting a false one.

6. **`COMMIT_DETECTED` volume was unbounded on a first scan.** A mature repository read
   whole would have written one event per commit. Capped at `_MAX_COMMIT_EVENTS`; the
   service-level cap is documented at the constant.

7. **A `metadata` naming collision was avoided by construction.** The Phase 7 defect
   (`metadata` reserved on the declarative class, ORM path vs. Core path disagreeing) is
   the reason the Phase 8 tables carry no `metadata` JSONB at all.

---

## 8. Environment limitations

Stated plainly, because several claims above depend on it.

- **Docker is not installed in this environment.** The container stack — `postgres`,
  `backend`, `frontend` in `docker-compose.yml` — **could not be executed at all.**
  `docker compose up` has never been run by anyone working on this phase. The compose file
  is only statically validated (`scripts/verify_compose.py`), which cannot tell you that the
  stack starts.
- **A native PostgreSQL was running**, not the container one. Every database-backed test
  that produced a number in this report really executed against a live PostgreSQL at
  `127.0.0.1:5432` on the `nexus_test` database. That is why `test_developer_api.py` and
  `test_developer_git_integration.py` report real pass counts rather than collection counts.
- **`alembic upgrade head` / `alembic check` were not run against the live database by the
  documentation agent.** `test_migration_ddl.py` renders the DDL offline and compares it to
  the models, which is real evidence about the DDL and **not** the same as having applied
  it. `test_migrations.py` (5 tests, `integration`-marked, contains the drift check) was
  part of the 155 that passed only in its non-database cases.
- **The git engine has never been exercised against a repository on a non-Windows
  platform.** See §10.
- **`git` itself is a platform binary and was available on this machine.** The engine is
  the only part of NEXUS that shells out, and it is the only part whose behaviour could
  differ between a developer's machine and a container image.

---

## 9. Cross-phase interaction with Phase 9

Phase 8 and Phase 9 ran concurrently against one tree, on disjoint files. Two couplings
exist and are recorded so a reader does not mistake them for accidents:

- **`career_evidence.repository_id` is a foreign key onto `git_repositories.id`**
  (`ON DELETE SET NULL`). It is the only reference from Phase 9 into Phase 8's schema, and
  it exists so career evidence can name the repository a piece of work was recorded
  against. `SET NULL` rather than `CASCADE`: deleting a repository must not quietly delete
  the user's record that they shipped something.
- **`career_features.v1.project_activity` is `null` when no repository has ever been
  scanned.** It is the contract's own worked example of the null-not-zero rule — `0` would
  assert that a repository exists and carries no commits, when the truth is that nobody has
  looked.

---

## 10. Known limitations

Each of these is a property of the shipped code, not of this environment.

- **A full rescan of a rewritten history needs `?full=true`.** The default scan is
  incremental — `git log --since` the stored `latest_commit_at` — because that is what
  keeps a rescan cheap and idempotent. After a history rewrite (`rebase`, `filter-branch`,
  a force-push) the stored high-water mark points at a commit that no longer exists, and an
  incremental read cannot recover from it. The route exposes `?full=true` for exactly this,
  and the query parameter's own description says when it is needed. Nothing detects the
  situation automatically, so after a rewrite the user must ask for a full scan.
- **`maintenance_activity` is computed without the preceding 90 days of file history,
  because no per-commit file table is stored.** The metric answers "commits that reached a
  file this record had not seen touched recently", with a 90-day lookback
  (`DEFAULT_MAINTENANCE_LOOKBACK_DAYS`). It reads `last_touched_before`, a
  `{(repository_id, path): last recorded change}` map the service supplies from *the
  commits this account has recorded*. A file somebody else last touched six months ago
  therefore reads as quiet here — which is a true statement about *this record* and is what
  the window means everywhere else in the module. The consequence is stated in the code as
  well: the metric reads **low** on a repository scanned before per-commit paths were
  stored, and a commit with no recorded file paths cannot be classified and does not count.
  Storing per-commit file rows would grow the table to millions of rows on a mature
  codebase, so the trade was made deliberately — but it is a trade.
- **The Windows subprocess handling costs a thread hop per git invocation.** This is the
  one place where two of this codebase's own architectural rules collide. NEXUS runs on a
  `SelectorEventLoop` on every platform, because psycopg's async driver needs
  `loop.add_reader` and asyncio's Windows default (`ProactorEventLoop`) does not provide
  it. But on Windows a `SelectorEventLoop` raises `NotImplementedError` from
  `subprocess_exec` — it has no subprocess transport at all. Without a workaround the git
  engine could not run at all on the platform its developers develop on. So
  `_running_loop_can_spawn()` detects the condition (a **class** test, not a trial call, so
  no exception from an unrelated cause is caught here) and `_run_git_on_worker_loop()` runs
  the identical `_run_git_here` on a private `ProactorEventLoop` from a worker thread. The
  fallback is deliberately the *same* function, so the timeout, the output ceiling, the kill
  and the stderr sanitiser all still apply and it cannot become a laxer scan. The private
  loop is constructed directly rather than through the active policy, because the policy is
  `nexus_loop_factory`, which hands back the very loop being worked around. On POSIX none
  of this runs.
- **Scans are user-triggered only.** There is no scheduler. Every figure on the surface is
  as of a named scan, and the page says how old that scan is rather than presenting it as
  current. The service boundary is already the seam a worker would take; only the caller
  changes.
- **Branch attribution is best-effort.** `git_branches.head_commit_hash` is read per
  branch and commits are attributed from the branch head that currently contains them, so a
  commit reachable from two branches is attributed to one. `git_commits.branch` is nullable
  for this reason and a commit with no resolvable branch is *absent* from a branch filter
  rather than misfiled.
- **A detached HEAD is a normal state, not a gap.** On a detached HEAD neither
  `is_current` nor `is_default` is true and `current_branch` is git's literal string
  `HEAD`.
- **`DELETE /developer/repositories/{id}` really deletes the history.** It is a cascade, not
  a deactivation, and that is the intended answer — but it is irreversible, and the
  `REPOSITORY_REMOVED` activity row survives the cascade so the account's own trail still
  shows it happened.
- **`last_scan_error` is a sentence, so it is not machine-parseable.** There is no
  structured error code on a scan run. A client branches on `last_scan_status`.

---

## 11. Starting point for Phase 10

- **`developer_features.v1` is the training set's developer half**, and it is stamped with
  its schema version so a trainer knows what each column meant without trusting the client
  that ordered them. Phase 9's `learning_features.v1` and `career_features.v1` join it.
- **The `activity_events` feed is the label set.** `COMMIT_DETECTED` — properly emitted,
  see §7 — is "this commit is new", which is a fact about the record rather than about the
  person, and that distinction is what makes the feed usable.
- **Do not add a feature that measures time.** Every column here is a count of commit
  objects, days carrying a commit, or lines git counted from a diff. A column derived from
  `committed_at` that treats two commits an hour apart as an hour of work would reintroduce
  the exact claim this schema was designed to be unable to make.

## See also

| Document | Contents |
| --- | --- |
| [`phase-9-learning-career-report.md`](./phase-9-learning-career-report.md) | The Phase 9 half of this brief |
| [`phase-7-report.md`](./phase-7-report.md) | The pattern these two reports follow, and the precedent for the idempotency and partial-unique-index arguments reused here |
| [`../architecture.md`](../architecture.md) | §17, the Developer Intelligence subsystem |
| [`../api-conventions.md`](../api-conventions.md) | Conventions Phases 8 and 9 established |
| [`../development.md`](../development.md) | §11, registering and scanning a local repository |