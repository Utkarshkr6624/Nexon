# NEXUS — Development guide

The rules for writing new code in NEXUS: how to get from a clean machine to a
running stack, what a normal edit-run-test cycle looks like, and — the part the
other documents deliberately do not cover — **the conventions a new endpoint,
page, migration or test has to follow.**

Everything here was read from the repository. Where a number appears, it came
from a command that was run; where something could not be run, it says so.

---

## How this fits with the other documents

Four documents, four non-overlapping jobs. If you are looking for something and
it is not here, it is in one of the other three.

| Document | Owns | Does not own |
| --- | --- | --- |
| [`../README.md`](../README.md) | Installation, environment variables, the command catalogue, troubleshooting | Conventions — *why* code is shaped the way it is |
| [`architecture.md`](architecture.md) | Structure and rationale: layering, request lifecycle, auth design, persistence, decisions | Step-by-step "how do I add X" procedures |
| **`development.md`** (this file) | Setup on a clean machine, the daily loop, adding an endpoint / page / migration / test, style and code conventions | Wire contract details, environment variables, troubleshooting |
| [`api-conventions.md`](api-conventions.md) | The endpoint contract: versioning, error envelope and codes, request ids, pagination, the endpoint checklist | Internal layering — which file raises the error |

Two consequences worth stating plainly:

- **The endpoint checklist is not repeated here.** `api-conventions.md` owns the
  wire contract — status codes, error shapes, auth requirements per route. This
  document owns the *code* contract: which layer a line of logic goes in, and
  which layer is allowed to know about what.
- **Troubleshooting is not repeated here.** If something is broken at runtime,
  the README's [Troubleshooting](../README.md#troubleshooting) section owns the
  diagnosis. This document tells you how not to break it.

---

## Contents

| Section | Contents |
| --- | --- |
| [1. Clean-machine setup](#1-clean-machine-setup) | Prerequisite checks, the bootstrap script, the database, verifying the install |
| [2. The daily loop](#2-the-daily-loop) | Edit → run → test, hot reload, what to run when |
| [3. Adding a backend endpoint](#3-adding-a-backend-endpoint) | The layering rule, the vertical slice, error translation |
| [4. Migrations](#4-migrations) | autogenerate → check → apply, and the rules about revision files |
| [5. Adding a frontend page](#5-adding-a-frontend-page) | The three-file pattern, the registry contract, real copy vs. sample data |
| [6. Testing conventions](#6-testing-conventions) | `integration` marker, fixtures, `assert_error_envelope`, the regression rule |
| [7. Design-system rules](#7-design-system-rules) | Tokens, `cva` variants, primitives, what not to build by hand |
| [8. Code conventions](#8-code-conventions) | ruff, docstrings, TypeScript strictness, the react-refresh constraint |
| [9. Before you open a pull request](#9-before-you-open-a-pull-request) | The checklist |
| [10. Verified baseline and known limits](#10-verified-baseline-and-known-limits) | What was actually executed, and what was not |

---

## 1. Clean-machine setup

### 1.1 What you need

| Requirement | Version | Used by |
| --- | --- | --- |
| Python | 3.13+ | backend, migrations, tests, scripts |
| Node.js | 20.19+ (22 LTS recommended) | frontend dev server, tests, build |
| npm | ships with Node | frontend |
| PostgreSQL | 16 (13+ works) | the backend, the integration tests |
| Docker + Compose v2 | recent | optional — only for the `make up` path |
| GNU make | 4.x | optional — the Makefile wraps nothing the scripts and npm do not already do |

`make` is not shipped with Windows. Run the raw commands instead; the README's
[Development commands](../README.md#development-commands) table has all of them.

### 1.2 Bootstrap

From the repository root:

```bash
python scripts/bootstrap.py                # everything
python scripts/bootstrap.py --skip-install # checks and .env only
```

The script is stdlib-only, so it runs on a bare interpreter — it has to, because
it is what creates the environment everything else depends on. In order
(`scripts/bootstrap.py`):

| Step | Function | Failure mode |
| --- | --- | --- |
| Python ≥ 3.13 | `check_python` | fatal |
| Node ≥ 20 and `npm` on PATH | `check_node` | fatal |
| Checkout layout (`backend/requirements.txt`, `backend/alembic.ini`, `frontend/package.json`) | `check_repository` | fatal |
| `.env` from `.env.example` | `ensure_env_file` | never overwrites an existing `.env` |
| `backend/.venv` | `ensure_venv` | reuses an existing virtualenv |
| `pip install -r backend/requirements.txt` | `install_backend` | fatal, names proxy/network as the usual cause |
| `npm install` in `frontend/` | `install_frontend` | fatal; warns if `package-lock.json` is missing |
| Docker availability | `report_docker` | **never fatal** — the stack also runs natively |

It is idempotent: re-running it is the correct response to almost any setup
problem.

**What it deliberately does not do:** it does not start PostgreSQL, create
`nexus_test`, run migrations, or start any server. Those are the next three
steps, in that order.

### 1.3 The database, in the right order

```bash
# from the repository root — wait for the server, then create the test database
backend/.venv/bin/python scripts/wait_for_db.py
backend/.venv/bin/python scripts/create_test_database.py

# migrate — alembic always runs from backend/
cd backend && ../backend/.venv/bin/python -m alembic upgrade head
```

On Windows the interpreter is `backend/.venv/Scripts/python.exe`.

`create_test_database.py` refuses to run if `TEST_DATABASE_URL` resolves to the
same database as `DATABASE_URL`, because the test suite truncates every managed
table. `tests/conftest.py` repeats that check (`_assert_separate_test_database`)
and aborts the session with `pytest.exit` if it ever trips — the script is
optional, the conftest is not.

You can skip this step entirely if you are using the Docker path: the backend
container runs `alembic upgrade head` on start, and `tests/conftest.py` creates
`nexus_test` itself.

### 1.4 Run it

```bash
./scripts/dev.sh              # backend + frontend, Ctrl-C stops both
./scripts/dev.sh backend      # API only, :8000
./scripts/dev.sh frontend     # Vite only, :5173
```

Or in two terminals: `cd backend && python run.py` and `cd frontend && npm run dev`.

> **Start the backend with `python run.py` from `backend/`.** A bare
> `uvicorn app.main:app` starts on Windows and then fails on every query, because
> psycopg's async driver needs `loop.add_reader` and the default Windows
> `ProactorEventLoop` does not provide it. `run.py` selects the loop through
> `app.core.event_loop.nexus_loop_factory`, which is also what
> `migrations/env.py` and `tests/conftest.py` use. There are exactly three
> consumers of that factory and a fourth would be a bug.

### 1.5 Verify the install

Run these before your first change. Every one of them is the same command the
pre-pull-request checklist asks for, so a green run here means you know what
"green" looks like.

```bash
# backend — from backend/
PY=../.venv/bin/python          # Windows: PY=../.venv/Scripts/python.exe
$PY -m ruff check .
$PY -m ruff format --check .
$PY -m pytest -m "not integration"

# frontend — from frontend/
npm run typecheck
npm run lint
npm test
npm run build
```

Then confirm the app, not just the tooling:

| Check | Where | Expected |
| --- | --- | --- |
| Liveness | `http://localhost:8000/health` | `{"status":"ok"}` — no database needed |
| Readiness | `http://localhost:8000/api/v1/health` | `status: "healthy"`, `database.status: "connected"` |
| UI | `http://localhost:5173` | Sign-in page; register an account from `/register` |

If `/health` is green but readiness says `degraded`, the backend is fine and the
database is not — that is the documented split, and the README's
[troubleshooting section](../README.md#the-database-is-unreachable) has the fix.

---

## 2. The daily loop

### 2.1 The cycle

| Stage | Command | Where |
| --- | --- | --- |
| Start both servers | `./scripts/dev.sh` | repository root |
| Backend hot reload | automatic, with `NEXUS_RELOAD=true` in `.env` | — |
| Frontend hot reload | automatic | — |
| Backend checks | `python -m ruff check . && python -m ruff format --check .` | `backend/` |
| Frontend checks | `npm run typecheck && npm run lint` | `frontend/` |
| Backend tests | `python -m pytest -m "not integration"` (fast) / `python -m pytest` (full) | `backend/` |
| Frontend tests | `npm test` | `frontend/` |

Throughout this document `python` means the project interpreter — `backend/.venv/bin/python`,
or `backend\.venv\Scripts\python.exe` on Windows — not whatever happens to be on `PATH`.

`NEXUS_RELOAD` defaults to `false` in `run.py`, so **set it to `true` for local
work** or you will be restarting the server after every edit. Compose forces it
to `false`, which is correct there: reload forks a second process, and a second
worker carries its own connection pool and its own empty revocation denylist.

### 2.2 Choosing the test command

| You changed | Run |
| --- | --- |
| Python, no schema or data | `python -m pytest -m "not integration"` — 145 tests, no database |
| A model, a repository, or anything touching data | `python -m pytest` — the full 188, needs PostgreSQL |
| TypeScript | `npm run typecheck && npm test` |
| A component's markup or a route | `npm test`, plus `npm run build` — `tsc -b` catches types and import paths, but only a real build proves the module graph resolves |

The `-m "not integration"` subset **must pass with PostgreSQL stopped.** That is
the point of the marker; if a test that could run offline has been marked
`integration` to make it pass, the suite has lost its most useful property.

### 2.3 Make targets

`make` is a thin wrapper — every target is a real command, and the logic lives in
`scripts/` and `package.json`. Use it if you have it:

```bash
make install     # scripts/bootstrap.py
make lint        # ruff check + ruff format --check + eslint + tsc -b
make test        # test-backend then test-frontend
make migrate     # alembic upgrade head
make backend     # scripts/dev.sh backend
```

On Windows, pass the interpreter explicitly — the Makefile default is the POSIX
layout:

```bash
make test-backend PY=backend/.venv/Scripts/python.exe
```

The full target list is in the
[README](../README.md#make-targets); `make help` prints it.

---

## 3. Adding a backend endpoint

Read the reference slice first. `backend/app/api/v1/auth.py` (router),
`backend/app/services/auth_service.py` (rules),
`backend/app/repositories/user.py` (SQL) and `backend/app/models/user.py` (table)
together are the only complete vertical slice in the repository, and a new module
should look like it.

### 3.1 The layering rule

**Dependencies point downward only.**

| Layer | Package | May import | Must not import |
| --- | --- | --- | --- |
| HTTP | `app/api/v1/*.py`, `app/api/deps.py` | FastAPI, `app.schemas`, `app.services`, `app.core` | SQLAlchemy, ORM internals, `app.repositories` internals |
| Domain | `app/services/*.py` | `app.schemas`, `app.repositories`, `app.core` | **FastAPI, ever** |
| Data | `app/repositories/*.py` | SQLAlchemy, `app.models` | HTTP, domain errors |
| Infrastructure | `app/core/*`, `app/db/*`, `app/models/*` | below itself | services, routers |

The domain layer's FastAPI ban is stated in the module docstring of
`app/services/auth_service.py` and is what keeps business rules testable without
a request. It is not aspirational — a `from fastapi import …` inside a service is
a layering violation, not a style preference.

`app/schemas/` sits on the HTTP boundary and is the only place Pydantic
validation runs.

### 3.2 Where the wiring goes

Two dependency modules, and the split matters:

| Module | Owns | Import it from |
| --- | --- | --- |
| `app/core/deps.py` | Identity: bearer scheme, `CurrentUser`, optional user, `SuperUser`, session → repository. Imports no service, which keeps the graph acyclic. | `app/api/deps.py` re-exports it |
| `app/api/deps.py` | Layering on top: `get_<module>_service` (session → repository → service), plus `get_authenticated_user`, which adds the revocation check | the routers |

A new module adds one provider here, mirroring `get_auth_service`:

```python
def get_project_service(repository: ProjectRepositoryDep) -> ProjectService:
    """Provide a request-scoped project service."""
    return ProjectService(repository)
```

Routers then depend on `Annotated[ProjectService, Depends(get_project_service)]`
and never construct a repository or a session themselves.

### 3.3 The error-translation rule

Two halves, and both matter:

| Layer | Rule | Reference |
| --- | --- | --- |
| Repository | **Never raises a domain error.** Returns `None` for a miss; an unexpected `IntegrityError` propagates untouched. | `UserRepository.get_by_id` returns `User \| None`; `app/repositories/user.py` states the rule in its module docstring |
| Service | **The single translation point.** Catches the raw driver error and raises the domain error the API contract promises. | `AuthService.register` catches `IntegrityError` and raises `ConflictError` |

Why it is drawn this way: one place decides that a duplicate e-mail is `409`
rather than a 500. If repositories translated too, the mapping would exist in
every module and drift. The cost accepted is that services must be prepared for
raw database exceptions — that trade is recorded in
[`architecture.md` §16](architecture.md#16-design-decisions).

Corollary: **a router raises nothing.** Routers pick a success status and return.
`NotFoundError`, `ConflictError`, `ForbiddenError` and `UnauthorizedError` come
out of the service; `install_exception_handlers` in `app/core/exceptions.py`
renders them. Never construct an error-shaped `JSONResponse` by hand.

### 3.4 Vertical-slice checklist

Order matters only in that the schema must exist before autogenerate sees the
model. Every item is a real file in the repository or a real command.

| # | Step | File or command |
| --- | --- | --- |
| 1 | Model: subclass `Base` with `UUIDPrimaryKeyMixin` and `TimestampMixin` | `app/models/<module>.py` |
| 2 | Migration | see [§4](#4-migrations) |
| 3 | Repository: SQL only, `None` on a miss, commit, then `refresh()` | `app/repositories/<module>.py` |
| 4 | Schemas: `<X>Create`, `<X>Update`, `<X>Read` with `model_config = ConfigDict(from_attributes=True)`; constraints here, so a bad payload is a 422 | `app/schemas/<module>.py` |
| 5 | Service: raise a domain error for every failure mode; normalise input here | `app/services/<module>_service.py` |
| 6 | Provider: `get_<module>_service` | `app/api/deps.py` |
| 7 | Router: `APIRouter(prefix="/<module>", tags=["<module>"])`, `response_model`, explicit `status_code`, one-line `summary` | `app/api/v1/<module>.py` |
| 8 | Register the router | `app/api/v1/router.py` — `api_v1_router.include_router(<module>.router)` |
| 9 | Tests | see [§6](#6-testing-conventions) |
| 10 | Frontend pairing: one function in `frontend/src/services/`, the wire type in `frontend/src/types/api.ts` | cross-link the endpoint checklist in [`api-conventions.md`](api-conventions.md#checklist-for-a-new-endpoint) |

Step 10 is the one most often forgotten, and the failure is a runtime mismatch
rather than a compile error — the backend schema and the TypeScript type are
checked by nothing except agreement.

### 3.5 The wire contract

Status codes, error envelopes, auth per route, pagination, request ids — all
owned by [`api-conventions.md`](api-conventions.md). Do not restate them here;
read that document's
[checklist for a new endpoint](api-conventions.md#checklist-for-a-new-endpoint)
before writing the route decorator, because it is more specific than anything
this document could say.

---

## 4. Migrations

[`backend/migrations/README.md`](../backend/migrations/README.md) is the
migration subsystem's own reference — command list, async-driver mechanics,
Windows notes. This section is the part a *change author* needs: the rules about
writing a revision rather than running one.

### 4.1 The workflow

All commands run from `backend/`.

```bash
# 1. edit the model
# 2. generate the revision from the model's metadata
python -m alembic revision --autogenerate -m "add widgets table"

# 3. read the generated file. Autogenerate is a first draft, not an answer.

# 4. prove there is no drift left
python -m alembic check

# 5. apply
python -m alembic upgrade head

# 6. roll back and forward again — a migration that only works going up is not done
python -m alembic downgrade -1
python -m alembic upgrade head
```

`python -m alembic check` exits non-zero when `Base.metadata` and the live schema
disagree. Note that it needs a reachable database; the same comparison is
asserted by `backend/tests/test_migrations.py::test_autogenerate_reports_no_drift`,
which is `integration`-marked and so only runs when one is available.

### 4.2 Rules for revision files

**A migration must not import a model.** `migrations/versions/0001_initial_create_users.py`
says so in its own module docstring, and it is the single most important rule in
this section: a revision is explicit DDL that happens to look like what the
model describes today. If it imported `app.models.user`, then editing the model
later would silently change the *meaning* of an already-applied migration — and
on a fresh database, or after a `downgrade`, that old revision would produce a
different schema than it did the day it was written.

Corollary: autogenerate output is a **draft**. Rename a column in a generated
`op.alter_column`, add `server_default=`, or add a comment, and the drift check
will still be happy. What you write by hand is what the migration means.

Autogenerate compares metadata against a live database and is wrong often enough
that an unreviewed generated revision is worse than a hand-written one. Two
failure modes to watch for:

| Autogenerate cannot | It emits instead | So |
| --- | --- | --- |
| See a rename | drop + add | Write the rename yourself (`op.alter_column(..., new_column_name=…)`) or the data is lost |
| Infer a data backfill | nothing | Write the `op.execute()` yourself, inside the revision |

Two further rules from the migration subsystem's own README:

- **`downgrade()` must work.** A migration you cannot roll back is one you
  cannot deploy safely — which is exactly what `downgrade -1` in the workflow
  above is checking.
- **One revision per logical change**, and an applied revision is never edited.
  Changing a model means adding a revision, not editing the one that created the
  table.

**`sqlalchemy.url` is empty on purpose.** `backend/alembic.ini` sets it to an
empty string, and the file's own header explains why: credentials must never
live in a git-tracked file. `migrations/env.py` resolves the URL through
`get_url()`, which falls back to `app.core.config.get_settings().sqlalchemy_database_uri`.
To migrate a different database, override the environment, not the ini file:

```bash
DATABASE_URL=postgresql+psycopg://nexus:nexus@127.0.0.1:5432/nexus_test \
  python -m alembic upgrade head
```

### 4.3 Naming and the chain

| Property | Value | Where |
| --- | --- | --- |
| File name | `<rev>_<slug>` (`file_template = %%(rev)s_%%(slug)s`) | `alembic.ini` |
| Slug length | 40 characters, trimmed | `alembic.ini` (`truncate_slug_length`) |
| Timestamps in filenames | off — generated names stay diff-friendly | `alembic.ini` (`revision_environment = false`) |
| Scope | schema `public` only; `alembic_version` and `spatial_ref_sys` excluded | `migrations/env.py` (`include_object`) |
| UUID rendering | `postgresql.UUID(as_uuid=True)`, dialect stated explicitly | `migrations/env.py` (`render_item`) |
| Comparison | `compare_type` and `compare_server_default` both on | `migrations/env.py` (`_autogenerate_options`) |

The chain has exactly one head and is asserted to be linear by
`backend/tests/test_migrations.py::test_the_migration_chain_is_linear_and_has_a_single_head`.

> **Known constraint, unchanged here.** That test asserts the full revision list
> literally — `== ["0001"]`. Adding a second revision makes it fail until the
> list is extended. That is a deliberate pin on the current chain, not an
> oversight, but it does mean a new migration comes with a one-line test update.
> It has been left as-is here because tests are outside this document's scope.

Drift is also a test, not just a command:
`test_autogenerate_reports_no_drift` compares the live schema against
`Base.metadata` with the same options `env.py` uses.

---

## 5. Adding a frontend page

Adding a module page is a **three-file change**. All three are required, and each
one missing produces a different loud failure rather than a blank page — which is
why the pattern is worth following exactly.

### 5.1 The three files

| # | File | What you add | Reference |
| --- | --- | --- | --- |
| 1 | `frontend/src/features/modules/catalog.ts` | a `ModuleDefinition` entry in `MODULES` (`to`, `label`, `summary`, `vision`, `phase`, `icon`, `capabilities`, `metrics`, `keywords`) and the matching `getModule('/<path>')` in one of the `NAV_GROUPS` | `MODULES` and `NAV_GROUPS` |
| 2 | `frontend/src/routes/lazy-pages.ts` | `export const <Name>Page = lazy(() => import('@/pages/<name>-page'))` | the whole file is this pattern |
| 3 | `frontend/src/routes/router.tsx` | a route object `{ path: '/<path>', element: <NewModulePage /> }` in the `AppLayout` children, plus the name in the import block | the `createBrowserRouter` table |

A page that is not a module placeholder needs no catalog entry; add it to the
route table alone. But if it appears in the sidebar or the command palette, it
needs all three.

### 5.2 The registry cannot drift from the route table

`getModule(path)` in `catalog.ts` **throws on an unknown path**. That is
deliberate and it is what makes the three-file pattern load-bearing:

```ts
export function getModule(path: string): ModuleDefinition {
  const match = MODULES.find((module) => module.to === path)
  if (!match) throw new Error(`Unknown module path: ${path}`)
  return match
}
```

| Failure | What happens |
| --- | --- |
| Route added, catalog entry missing | `getModule()` throws at render; the sidebar, palette and header all call it |
| Catalog entry added, route missing | `NAV_GROUPS` renders a link to a path the router resolves to `*` → `NotFoundPage` |
| Both present, `to` values disagree by a character | the first case again — the registry is the authority on paths |

`NAV_GROUPS` calls `getModule()` at module scope, so a malformed `to` in a group
fails at import time rather than on click.

### 5.3 The page file itself

The whole of a placeholder page:

```tsx
// frontend/src/pages/projects-page.tsx
import { getModule } from '@/features/modules/catalog'
import { ModulePage } from '@/pages/module-page'

export default function ProjectsPage() {
  return <ModulePage module={getModule('/projects')} />
}
```

Three conventions:

1. **Pages use a default export.** `lazy(() => import(...))` needs one.
   Primitives in `components/ui/` use named exports.
2. **The page passes `children` for anything module-specific**, appended below
   the standard body — `ModulePage` accepts `ModulePageProps.children`. Do not
   fork the placeholder layout to add one card.
3. **Copy comes from the registry.** `summary`, `vision`, `capabilities` and
   `metrics` are real, module-specific sentences written for that module. A page
   that invents its own header copy has forked the registry for no reason.

### 5.4 When the page becomes live

A placeholder page that starts fetching needs, in addition:

| Layer | File | Rule |
| --- | --- | --- |
| Wire types | `frontend/src/types/api.ts` | mirror the Pydantic schemas, same `snake_case` spelling |
| Transport | `frontend/src/services/<module>.ts` | one exported function per endpoint; no `fetch` outside `lib/api-client.ts` |
| Server state | a hook in `frontend/src/features/<module>/` | TanStack Query; the query client suppresses retries for 4xx |
| Error handling | `ApiError` | branch on `code`, never on `message` |

### 5.5 No fabricated data

**A module page renders real copy, not invented numbers.** Every metric tile on
a placeholder renders an em dash and states what it will need:

```tsx
<p className="…text-muted-foreground/50">—</p>
<p className="…text-muted-foreground">{hint}</p>   // e.g. "Requires project records"
```

and the panel beside it says *"Nothing is stored here until Phase N."* This is a
standing rule, not a placeholder to be cleaned up later. A screenshot with a
plausible-looking `47` in it is a lie the code cannot retract, and the next
person to read the page has no way to tell it from a real measurement. If a
number is not backed by a query, it is an em dash.

---

## 6. Testing conventions

### 6.1 The `integration` marker

Registered in `backend/pytest.ini`:

```ini
markers =
    integration: requires a live PostgreSQL instance
```

`--strict-markers` is on, so an unregistered marker is an error rather than a
no-op.

| Rule | Detail |
| --- | --- |
| What to mark | Any test that touches the database — which in practice means any test whose signature pulls in `client`, `db_session` or `truncated_database` |
| How | Module-level `pytestmark = pytest.mark.integration`, as in `test_auth.py`, `test_repositories.py`, `test_errors.py`, `test_migrations.py`; per-test `@pytest.mark.integration` where only one test needs it |
| What must pass offline | `python -m pytest -m "not integration"` with PostgreSQL stopped |
| Current split | 145 offline, 43 integration, 188 collected |

Mark a test `integration` because it genuinely needs a database — not because it
is easier to get green that way. The offline subset is the fast inner loop; a
contributor who cannot run it cannot iterate.

### 6.2 Choosing a client fixture

The three HTTP fixtures differ in two ways, and both matter:

| Fixture | Transport | Database | Use for |
| --- | --- | --- | --- |
| `offline_client` | `ASGITransport(app)` | none | Anything that must pass with PostgreSQL down: `/health`, error-envelope shapes, logging, middleware, config-driven routes |
| `client` | `ASGITransport(app)` | `engine` + `truncated_database` | Routes that read or write data |
| `non_raising_client` | `ASGITransport(app, raise_app_exceptions=False)` | none (add `truncated_database` if needed) | Tests that deliberately make the app raise |

`ASGITransport` defaults to `raise_app_exceptions=True`, which re-raises inside
the test — so the rendered 500 the user would have received is never observable,
and the `internal_error` catch-all handler cannot be asserted on at all. That is
the only reason `non_raising_client` exists.

None of them runs the application lifespan. Do not write a test that assumes a
startup or shutdown hook fired; the `engine` fixture installs the test database
behind `app.db.session` instead, which is what the lifespan would have done.

### 6.3 Database fixtures

| Fixture | Scope | Behaviour |
| --- | --- | --- |
| `test_database_url` | session | Creates `nexus_test` if missing (via an AUTOCOMMIT connection to the `postgres` maintenance database), then `alembic upgrade head` |
| `engine` | session | `NullPool` engine on the test database, installed as the application's engine for the whole session, restored on teardown |
| `truncated_database` | function | `TRUNCATE … RESTART IDENTITY CASCADE` over every table in `Base.metadata.sorted_tables` |
| `db_session` | function | An `AsyncSession` on that database |
| `settings` | function | Clears the `get_settings` `lru_cache` around the test so a monkeypatched variable cannot leak |
| `make_settings` | function | Builds a `Settings` from explicit `monkeypatch.setenv` overrides |

Three rules follow from how these are built:

- **`Base.metadata.create_all` is never used.** The schema under test comes from
  the migrations, or it proves nothing about the migration.
- **`NullPool` is required, not an optimisation.** `pytest.ini` scopes the asyncio
  loop to a single test, so a pooled connection opened under one loop would be
  reused under the next.
- **Repositories commit.** A `db_session` fixture's `rollback()` does not undo a
  committed write — that is what `truncated_database` is for.

### 6.4 Asserting on an error

`assert_error_envelope` asserts the whole contract, not just the status:

```python
error = assert_error_envelope(response, status_code=409, code="conflict")
assert error["request_id"] == response.headers["X-Request-ID"]
```

It checks the status, that the top-level payload is exactly `{"error": …}`, that
the error object has exactly `code` / `message` / `details` / `request_id`, that
the message is a non-empty string, and that the body's `request_id` equals the
response header. Use it for **every** failure path, not the interesting one.

`assert_no_internals` is a plain function in `tests/test_errors.py`, not a fixture —
import it (`from tests.test_errors import assert_no_internals`) when you need it.
It fails if the body contains any fragment from `FORBIDDEN_FRAGMENTS`: a
traceback, a driver or ORM name (`psycopg`, `sqlalchemy`, `asyncpg`, `alembic`),
a SQL keyword, a file-path fragment, a column name like `users.email`, or an
internal package path. Pair it with `assert_error_envelope`.

### 6.5 Frontend tests

| Property | Value |
| --- | --- |
| Runner | Vitest, `jsdom`, `globals: false` — import `describe`/`it`/`expect` from `vitest` |
| Config | `test` block in `frontend/vite.config.ts`; `include: ['src/**/*.{test,spec}.{ts,tsx}']` |
| Setup | `src/test/setup.ts`, applied per test: `@testing-library/jest-dom/vitest`, `cleanup()`, and shims for `scrollIntoView`, `matchMedia` and `AbortSignal` |
| Location | Colocated next to the subject (`components/ui/button.test.tsx`), not in a `__tests__` folder |
| Current suite | 10 files, 30 tests |

Individual test files should not add their own environment shims — the setup
file installs them in `beforeEach` and `unstubAllGlobals` in `afterEach` would
otherwise tear them down. Stub what a test needs through `vi.stubGlobal` /
`vi.spyOn`; the teardown handles the rest.

### 6.6 The regression rule

**A bug fix lands with a test that fails when the fix is reverted.**

That is the whole criterion: revert the one line that fixed it, run the new test,
watch it go red, put the line back, watch it go green. If it passes both ways it
is not testing the fix. The same applies to a feature guard — a test that cannot
fail is a comment that costs a test run on every future change.

In practice that means a new test that:

- asserts the specific behaviour that was broken, not the general happy path;
- exercises the real code path — the real `offline_client`/`client`, the real
  component render — rather than a mock of it;
- and, for an envelope failure path, uses `assert_error_envelope` so it also
  guards the shape.

---

## 7. Design-system rules

### 7.1 Tokens come from `index.css`, not from a class

`frontend/src/index.css` defines **bare HSL channels** — `212 85% 50%`, not
`hsl(212 85% 50%)` — in both `:root` and `.dark`. `frontend/tailwind.config.ts`
maps them with `<alpha-value>`:

```ts
primary: {
  DEFAULT: 'hsl(var(--primary) / <alpha-value>)',
  foreground: 'hsl(var(--primary-foreground) / <alpha-value>)',
},
```

That is what makes `bg-primary/10` work at all. **The consequence for you:** to
add a colour, add the channel triplet to `:root` *and* `.dark` in `index.css`,
then add the mapping in `tailwind.config.ts`. Do not write `bg-[hsl(212_85%_50%)]`
in a component — it will not respond to the theme.

Radius and typography are tokens too (`--radius` drives the `lg`/`md`/`sm`
`borderRadius` scale; `fontFamily.sans` is Inter with system fallbacks).

### 7.2 Primitives are shadcn-style, over Radix

`frontend/src/components/ui/` holds the primitives. They follow the shadcn/ui
convention (configured in `frontend/components.json`, new-york style, lucide
icons): the component source lives in this repository rather than in
`node_modules`, so a change to a primitive is a normal edit.

The pattern, from `button.tsx`:

```tsx
const buttonVariants = cva('…base classes…', {
  variants: {
    variant: { default: '…', secondary: '…', outline: '…', ghost: '…', destructive: '…' },
    size:    { sm: '…', default: '…', lg: '…', icon: '…' },
  },
  defaultVariants: { variant: 'default', size: 'default' },
})

export interface ButtonProps
  extends React.ComponentPropsWithoutRef<'button'>,
    VariantProps<typeof buttonVariants> { … }
```

Rules that follow from it:

| Rule | Why |
| --- | --- |
| Styling variation is a `cva` variant, not a conditional `className` at the call site | One place decides what "secondary" means; a caller cannot half-apply it |
| New variants are added to the map, not invented per use | A page that needs a sixth variant gets a sixth variant |
| Component-level overrides go through `className`, merged with `cn` | `cn` is `twMerge(clsx(...))` (`lib/utils.ts`) — later utilities win, so `className="w-full"` really does override a `size` width |
| `asChild` renders the child with the styles, via Radix `Slot` | `<Button asChild><Link/></Button>` is the correct composition; nesting a button in an anchor is not |
| Forward refs; set `displayName` | Radix and the React DevTools both depend on them |

### 7.3 Structural helpers, and what not to build by hand

`index.css` also owns a small `@layer components` block:

| Helper | Use |
| --- | --- |
| `.app-container` | Page gutter and max width (`1400px`, responsive padding). The dashboard, settings, not-found and module pages all wrap their outermost element in it; the login and register pages use `AuthShell` instead |
| `.app-scroll` | The scroll region inside the app shell, so the page does not scroll twice |

Beyond that, prefer the existing building blocks over a new one:

| Need | Use |
| --- | --- |
| Empty or waiting state | `components/feedback/empty-state.tsx` |
| Loading | `components/feedback/loading-state.tsx` (framed region) or `components/ui/skeleton.tsx` (inline placeholder) |
| Error | `components/feedback/error-state.tsx` — retryable, and it reads `ApiError` |
| Unhandled render error | `components/feedback/app-error-boundary.tsx`, wired as the router's `errorElement` |
| Page title block | `components/feedback/page-header.tsx` — takes `eyebrow`, `title`, `description`, `badges` |
| A modal, menu, tooltip, separator, avatar | The matching `components/ui/` primitive |

Accessibility is not optional styling. `:focus-visible` is defined globally in
`index.css` so focus is visible on native *and* Radix elements; `prefers-reduced-motion`
collapses durations rather than removing transitions. Decorative icons carry
`aria-hidden="true"` (`module-page.tsx` does this on every icon it renders) so
they are not announced.

---

## 8. Code conventions

### 8.1 Backend — ruff

Configured in `backend/pyproject.toml`. Both `check` and `format --check` must be
clean; `migrations/` is excluded because Alembic boilerplate is not ours to lint.

| Setting | Value |
| --- | --- |
| Line length | 100 (`[tool.ruff] line-length`) — and `E501` is *ignored*, because the formatter owns line length |
| Target | `py313` |
| Import sorting | isort (`I`), `known-first-party = ["app"]` |
| Docstrings | pydocstyle (`D`), **Google convention** |
| Also enabled | pycodestyle, pyflakes, pep8-naming, pyupgrade, bugbear, builtin-shadowing, comprehensions, simplify, ruff-specific, async, **bandit (`S`)** |

Bandit is on, so findings like a hardcoded credential, an `assert` outside
`tests/`, or an unchecked subprocess call are lint errors in `app/`. `S105` is
ignored because a settings default may legitimately look like a hardcoded secret.

### 8.2 Docstrings explain why

The convention is enforced by the *quality* rules (`D2xx`, `D4xx`) while the
*presence* rules are relaxed: `D105` (magic methods) and `D107` (`__init__`) are
ignored, and `tests/*` is exempted from docstring requirements. The rationale is
written into `pyproject.toml`: demanding a docstring on every dunder or Pydantic
validator produces noise, not information.

So: module docstrings and public functions get one, and they say something a
reader could not infer from the code.

| Weak | Strong |
| --- | --- |
| `"""Register a user."""` | `"""Create an account, rejecting an address that is already taken."""` |
| `"""Caches the hash."""` | `"""Return a bcrypt hash of a random value, computed once per process. …so that the missing-account path pays the same bcrypt cost as a real check…"""` — `_decoy_hash`, `auth_service.py` |

The module docstring of `auth_service.py` is the model: it says what the module
owns *and* what it refuses to do (import FastAPI), and that is why the file can
be read as a rule rather than a list of functions.

Inline comments follow the same rule — `backend/app/repositories/user.py`
explains why `create()` calls `refresh()`, not that it does.

### 8.3 Backend — imports and typing

| Convention | Detail |
| --- | --- |
| First-party | `app` is the only first-party name; import from the package, not by path |
| Forward references | `from __future__ import annotations` at the top of every non-empty module in `app/`, `tests/` and `migrations/` (the only exceptions are the empty package `__init__.py` files) |
| Async | `async def` all the way down through router → service → repository; nothing blocks the loop |
| Timezone-aware datetimes | `datetime.now(UTC)`, never naive `utcnow()` |
| Errors | raise a `NexusError` subclass from `app.core.exceptions`, with a `raise … from exc` when wrapping |
| Line length | 100 columns, enforced by the formatter — run it, do not hand-wrap |

### 8.4 Frontend — TypeScript

`frontend/tsconfig.app.json` is strict in the ways that catch real bugs:

| Option | Consequence for you |
| --- | --- |
| `strict` | No implicit `any`, no implicit `undefined` from a possibly-absent value |
| `noUncheckedIndexedAccess` | `arr[0]` is `T \| undefined`. Narrow it — a bare `arr[0].name` will not compile |
| `verbatimModuleSyntax` | Type-only imports must say `import type { … }`. `import { type Foo }` is the wrong form |
| `noUnusedLocals`, `noUnusedParameters` | An unused import is an error, not a hint |
| `noEmit` | Vite owns emit; `tsc -b` only type-checks |
| `paths` | `@/*` maps to `src/*` — always import through it, never by relative path out of a subtree |

Runtime types come from `src/types/api.ts` and mirror the Pydantic schemas in
`snake_case`, with no aliases and no camelCase bridge.

### 8.5 The `react-refresh` constraint

`frontend/eslint.config.js` turns on `react-refresh/only-export-components` as an
**error**. It exists so Vite's fast refresh can swap a component without
re-executing the module — and it will bite you the first time you export a helper
next to a component:

```
Fast refresh only works when a file only exports components.
```

**Where helpers must live, then:**

| Want | Do this |
| --- | --- |
| A non-component helper used by a component | Move it to its own module — `features/`, `lib/`, or a sibling file — and import it |
| A `cva` variant map | Export it from the primitive file, but add its name to `allowExportNames` in `eslint.config.js` — `buttonVariants`, `badgeVariants` and `spinnerVariants` are already there |
| A constant next to a component | Allowed: `allowConstantExport: true` covers exported constants |

The whitelist is an explicit, reviewed list, not a pattern. A new variant map
requires editing `eslint.config.js` in the same commit — which is the intended
friction: it makes the decision visible.

### 8.6 Naming

| Thing | Convention | Example |
| --- | --- | --- |
| Python modules, functions, variables | `snake_case` | `auth_service.py`, `get_authenticated_user` |
| Python classes | `PascalCase` | `AuthService`, `RevocationStore` |
| React components, types | `PascalCase` | `ModulePage`, `ModuleDefinition` |
| Files and directories | `kebab-case` | `module-page.tsx`, `api-client.ts`, `use-health.ts` |
| Exported types | `PascalCase`, usually the component name plus `Props` or the domain noun | `ButtonProps`, `ModuleCapability` |
| Hooks | `use-` prefix, file named after the hook | `use-health.ts` |

---

## 9. Before you open a pull request

| # | Check | Command | Where |
| --- | --- | --- | --- |
| 1 | Backend lint | `python -m ruff check .` | `backend/` |
| 2 | Backend format | `python -m ruff format --check .` | `backend/` |
| 3 | Frontend lint | `npm run lint` | `frontend/` |
| 4 | Frontend types | `npm run typecheck` | `frontend/` |
| 5 | Backend tests | `python -m pytest -m "not integration"` | `backend/` |
| 6 | Backend tests, full | `python -m pytest` — only if PostgreSQL is running | `backend/` |
| 7 | Frontend tests | `npm test` | `frontend/` |
| 8 | Migration drift | `python -m alembic check` — **required if you touched a model** | `backend/` |
| 9 | Build | `npm run build` — required if you touched routing, imports or a chunk rule | `frontend/` |

`make lint` covers 1–4 and `make test` covers 5–7.

And, not command-shaped:

- [ ] Every failure path you added is asserted with `assert_error_envelope`.
- [ ] Every bug fix landed with a test that fails when the fix is reverted
      ([§6.6](#66-the-regression-rule)).
- [ ] Nothing fabricated: no placeholder renders a number it cannot source
      ([§5.5](#55-no-fabricated-data)).
- [ ] The wire contract still holds — errors raised from the service, not the
      router; schemas own their constraints.
- [ ] New lines are below the DEV MARKER side of `backend/requirements.txt`, or
      above it and genuinely needed at runtime. The Dockerfile strips everything
      below the marker.
- [ ] `scripts/verify_compose.py` still passes if you touched
      `docker-compose.yml` or `.env.example`.

---

## 10. Verified baseline and known limits

These are the numbers this document was written against. They are results, not
projections.

| Command | Working directory | Result |
| --- | --- | --- |
| `pytest -m "not integration"` | `backend/` | **145 passed, 43 deselected** (188 collected) |
| `ruff check .` | `backend/` | clean, 44 files |
| `ruff format --check .` | `backend/` | clean, 44 files |
| `npm run typecheck` | `frontend/` | clean |
| `npm run lint` | `frontend/` | clean |
| `npm test` | `frontend/` | 10 files, **30 tests** passing |
| `npm run build` | `frontend/` | succeeds |
| `python scripts/verify_compose.py` | repository root | passes — 14 Compose variables, all documented in `.env.example` |

Uncompressed `frontend/dist/assets/` chunk sizes from that build:

| Chunk | Bytes |
| --- | --- |
| `react` | 222,295 |
| `radix` | 113,444 |
| `router` | 91,288 |
| `index` (entry) | 84,474 |
| `data` | 35,764 |
| `icons` | 13,240 |
| `dashboard-page` | 11,903 |
| `settings-page` | 4,828 |
| per-page placeholder chunks | ~0.36 kB each |

A page that renders nothing but the registry is ~0.36 kB, because the code
lives in `ModulePage` and the registry. That is the shape of the code-splitting
strategy working, and a useful signal: if a placeholder chunk grows by kilobytes,
something page-specific has crept in.

### What was **not** verified here

The environment this baseline was captured in had **no PostgreSQL and no Docker**.
That means:

| Not run | Why it matters |
| --- | --- |
| `pytest` in full (the 43 integration tests) | The offline subset is green; the database-backed half has not been executed there. Run it before trusting a change to a model, repository or migration |
| `docker compose up` | `docker-compose.yml` has never been executed by `docker compose`. `scripts/verify_compose.py` validates it statically — Compose v2 syntax, three services, real build contexts, existing bind mounts, every `${VAR}` documented — and cannot tell you the stack starts. Treat the first run as untested |
| `alembic check` against a live database | The drift assertion in `test_migrations.py` covers it, but that test is integration-marked and so was not run |

Nothing in this document should be read as a claim that those three were
exercised. They are the parts that still need a machine with a database.

---

## See also

| Document | Contents |
| --- | --- |
| [`../README.md`](../README.md) | Prerequisites, quick start, environment variables, the command catalogue, troubleshooting, roadmap |
| [`architecture.md`](architecture.md) | Layering rationale, request lifecycle, error contract, auth design, persistence, decisions and the cost each one accepts |
| [`api-conventions.md`](api-conventions.md) | Base URL and versioning, the error envelope and its code table, request ids, pagination, the endpoint checklist |
