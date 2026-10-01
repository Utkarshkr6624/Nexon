# NEXUS

**Personal Intelligence & Decision Platform** — a local-first, single-user platform for
projects, planning, knowledge, analytics and machine learning. It runs entirely on your
own machine and is never deployed to a cloud. No paid APIs, no paid services.

---

## Status: Phase 1 (technical foundation) is complete

Phase 1 delivers the *foundation only*. What exists today:

| Area | State |
| --- | --- |
| Repository layout, tooling, lint/format rules | Done |
| FastAPI backend skeleton, `/api/v1` routing, layered architecture | Done |
| JWT auth (register / login / refresh / logout / me) with `users` table | Done |
| Alembic migration pipeline | Done |
| PostgreSQL 16 via Docker Compose, three-service stack | Declared and statically validated; never executed by `docker compose` |
| React + TypeScript frontend: router, app shell, design system, theming | Done |
| Structured logging, `X-Request-ID` correlation, shared error envelope | Done |
| Health/readiness endpoints, Swagger/ReDoc | Done |
| Automated tests | Passing — 145 backend + 30 frontend; 43 backend `integration` tests need PostgreSQL and have not been run |

The 145 backend figure is `pytest -m "not integration"` from `backend/`; the other 43 tests
are marked `integration` and need a live PostgreSQL. `frontend/` runs 30 tests across 10
files with `npm test`. Neither a database nor Docker was available on the machine this
document was written on, so nothing that needs either was executed.

**What does not exist yet.** Projects, Tasks, Planner, Knowledge, Search, Analytics,
Developer, Learning, Career, AI Assistant and Experiments are *designed placeholder pages
only*. They render a real module description, the planned capabilities and the phase in
which they ship — but they store nothing, compute nothing, and read no data. Every metric
tile on those pages renders an em dash on purpose; no sample data is fabricated. Outside the
placeholder set, the live pages are Login and Register (real calls to the auth endpoints),
Dashboard (service health polled from the API) and Settings (theme preference and the
signed-in session).

Do not build on this README as if the product modules were functional. See
[Roadmap](#roadmap).

---

## Architecture at a glance

Everything runs on one machine. There is no external service of any kind.

```
        ┌────────────────────────── your machine ──────────────────────────┐
        │                                                                  │
        │   Browser ──── :5173 ───► Vite dev server (React 19 + TypeScript) │
        │                              │                                   │
        │                              │ proxies /api and /health          │
        │                              ▼                                   │
        │   Browser ──── :8000 ───► FastAPI (uvicorn)                     │
        │                              │                                   │
        │                              │ SQLAlchemy 2.x async (psycopg 3)  │
        │                              ▼                                   │
        │                         PostgreSQL 16 ──── :5432                │
        │                                                                  │
        │   .env ──► pydantic-settings ──► both processes                  │
        │   docker-compose.yml ──► postgres | backend | frontend           │
        └──────────────────────────────────────────────────────────────────┘
```

Three containers, wired by health checks:

| Service | Published port | Image / build | Starts when |
| --- | --- | --- | --- |
| `postgres` | `${BIND_HOST}:${POSTGRES_PORT}:5432` | `postgres:16-alpine`, named volume `nexus_pgdata` | — |
| `backend` | `${BIND_HOST}:8000:8000` | build `./backend` | `postgres` is healthy |
| `frontend` | `${BIND_HOST}:5173:5173` | build `./frontend` (Vite dev server) | `backend` is healthy |

`BIND_HOST` defaults to `127.0.0.1`, so all three ports are reachable from this machine
only. Widening it to `0.0.0.0` publishes the database, the API and the dev server to every
host on the network, in front of the default `nexus`/`nexus` credentials and the
placeholder `SECRET_KEY`. See [`BIND_HOST`](#environment-variables).

The backend container's start command is `alembic upgrade head && exec python run.py`, so a
fresh volume is migrated before the API answers.

### Repository map

```
Nexo/
├── .env.example            source of truth for every environment variable
├── Makefile                thin wrappers around every dev command
├── docker-compose.yml      postgres + backend + frontend
├── scripts/                bootstrap, db wait, test-db creation, compose check, dev launcher
├── docker/postgres/init/   pg_trgm + unaccent, applied on first init only
├── docs/
│   ├── architecture.md
│   ├── development.md
│   └── api-conventions.md
├── backend/
│   ├── run.py              entrypoint — selects the psycopg-compatible event loop
│   ├── alembic.ini         no credentials; the URL comes from settings
│   ├── migrations/         Alembic env + versions (0001_initial_create_users)
│   ├── requirements.txt    runtime deps above the DEV MARKER, dev deps below
│   ├── pyproject.toml      ruff configuration
│   ├── pytest.ini          testpaths, asyncio mode, `integration` marker
│   ├── Dockerfile          multi-stage; strips dev deps at the DEV MARKER
│   ├── tests/              pytest suite
│   └── app/
│       ├── main.py         application factory, lifespan, /health and /
│       ├── api/
│       │   ├── router.py              mounts /api/v1
│       │   ├── deps.py                HTTP-layer wiring: session → repo → service
│       │   └── v1/{router,health,auth}.py
│       ├── core/
│       │   ├── config.py              typed settings (pydantic-settings)
│       │   ├── exceptions.py         domain errors + shared error envelope
│       │   ├── event_loop.py         SelectorEventLoop on Windows for psycopg
│       │   ├── logging.py            JSON/console logging, redaction, request_id
│       │   ├── middleware.py          X-Request-ID, timing, access log
│       │   ├── security.py            bcrypt + JWT issue/verify
│       │   └── deps.py                canonical auth dependencies
│       ├── db/{base,session}.py       Base + mixins, async engine and session
│       ├── models/user.py            SQLAlchemy ORM model
│       ├── repositories/user.py      SQL only
│       ├── schemas/{common,health,user}.py   Pydantic request/response models
│       └── services/{auth_service,user_service}.py   business rules
└── frontend/
    ├── vite.config.ts      dev proxy (:8000), code splitting, Vitest config
    ├── tailwind.config.ts  token → utility mapping
    ├── components.json     shadcn/ui configuration (new-york, lucide)
    ├── eslint.config.js
    ├── Dockerfile          node:22-alpine running the Vite dev server
    └── src/
        ├── main.tsx        React root
        ├── app/            providers, query client, theme provider, auth bootstrap
        ├── routes/         router, layouts, guards, lazy route table
        ├── pages/          one file per route (many are placeholders)
        ├── features/       domain logic: health, modules catalog, command palette
        ├── components/
        │   ├── ui/         design-system primitives (shadcn-style)
        │   ├── layout/     app shell, sidebar, top bar, menus, palette
        │   ├── feedback/   loading / empty / error states, page header
        │   └── brand/      logo
        ├── hooks/          use-command-palette, use-debounce, use-media-query
        ├── lib/            api-client.ts, utils.ts (cn)
        ├── services/       auth.ts, health.ts, errors.ts — one function per endpoint
        ├── stores/         Zustand auth and theme stores
        ├── types/          wire types mirroring the backend schemas
        └── index.css       design tokens (HSL channels) + structural helpers
```

---

## Stack

| Layer | Choice | Notes |
| --- | --- | --- |
| Language (backend) | CPython 3.13+ | `requires-python = ">=3.13"` |
| Web framework | FastAPI 0.142 | async, OpenAPI-first |
| ASGI server | uvicorn 0.54 | started through `backend/run.py` |
| ORM | SQLAlchemy 2.1 (async) | `postgresql+psycopg` |
| Driver | psycopg 3.3 | binary wheel; requires a SelectorEventLoop on Windows |
| Migrations | Alembic 1.20 | async engine, URL injected from settings |
| Validation / config | Pydantic 2.13 + pydantic-settings | one typed `Settings` object |
| Auth | PyJWT 2.15 + bcrypt 5.0 | HS256, access + refresh, in-process denylist |
| Database | PostgreSQL 16 (`postgres:16-alpine`) | `pg_trgm`, `unaccent` enabled on first init |
| Lint / format (backend) | ruff 0.16 | `check` + `format --check`, line length 100 |
| Tests (backend) | pytest 9.1 + pytest-asyncio + httpx | 145 tests without a database; 43 more are marked `integration` and need PostgreSQL |
| Framework (frontend) | React 19 | function components, StrictMode |
| Language (frontend) | TypeScript 5.7 | `strict`, `noUncheckedIndexedAccess`, `verbatimModuleSyntax` |
| Build / dev server | Vite 7 | dev proxy, manual chunks, Vitest config |
| Routing | react-router-dom 7 | `createBrowserRouter` |
| Server state | TanStack Query 5 | retries, caching, polling |
| Client state | Zustand 5 | auth session, theme, command palette |
| Styling | Tailwind CSS 3.4 + shadcn/ui conventions | Radix primitives, `cva` variants, lucide icons |
| Charts | recharts 2.15 | reserved for the Analytics module |
| Tests (frontend) | Vitest 3.2 + React Testing Library | 30 tests in 10 files |

Production bundle is code-split per route and by vendor group. Current build, uncompressed
`frontend/dist/assets/` sizes: entry chunk `index` 84,474 B, largest vendor chunk `react`
222,295 B, then `radix` 113,444 B, `router` 91,288 B and `data` 35,764 B. The largest real
page chunk is `dashboard-page` at 11,903 B; the ten stub module pages are ~0.36 kB each and
`search-page` — which renders a disabled input pointing at Phase 4 — is 1,239 B.

---

## Prerequisites

| Requirement | Version | Needed for |
| --- | --- | --- |
| Python | 3.13+ | backend, migrations, tests, scripts |
| Node.js | 20.19+ (22 LTS recommended) | frontend, tests, build |
| npm | ships with Node | frontend |
| Docker + Compose v2 | recent | the `docker compose` path (optional) |
| PostgreSQL | 16 (or a local 13+) | the non-Docker path |
| GNU make | 4.x | optional; the Makefile is a convenience only |

`make` is not shipped with Windows. Either install it (Git Bash make package, Chocolatey,
Scoop) or run the raw commands listed under [Development commands](#development-commands) —
the Makefile contains nothing the scripts and npm do not already do.

---

## Quick start

### Path A — Docker Compose (everything in containers)

Requires Docker with the Compose v2 plugin, and a `.env` in the repository root — the
compose file passes it to the containers, so `cp .env.example .env` is not optional.

```bash
# from the repository root
cp .env.example .env
docker compose up -d --build
```

Then open:

| What | URL |
| --- | --- |
| Application | http://localhost:5173 |
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| Liveness | http://localhost:8000/health |
| Detailed health | http://localhost:8000/api/v1/health |

Those URLs work because the published ports bind to `127.0.0.1` by default
(`BIND_HOST`). Create an account from the **Authorize** button in Swagger, or from the
register page in the UI.

Follow the logs, and stop the stack:

```bash
docker compose logs -f
docker compose down          # keeps the database volume
docker compose down -v       # also drops nexus_pgdata
```

> **This path has never been run.** `docker-compose.yml` was authored on a machine without
> Docker. It is parsed and structurally validated by `scripts/verify_compose.py`, but no
> `docker compose` command has ever executed it. Treat your first `up` as untested, and
> start with `docker compose logs -f`. See
> [Troubleshooting](#docker-compose-up-fails-before-anything-starts).

#### What `docker-compose.yml` interpolates, and what it deliberately does not

`.env` reaches the stack by two different mechanisms, and the difference matters:

| Mechanism | Where | Effect |
| --- | --- | --- |
| `${VAR:-default}` | throughout the file | Interpolated while Compose parses the YAML. The container sees the value written here and nothing else. |
| `env_file: .env` | `backend`, `frontend` | The whole file is handed to the container, so the ~20 settings no `environment:` entry names — `DEBUG`, `APP_NAME`, `OPENAPI_URL`, `JWT_*`, `DB_POOL_*`, `DB_PROBE_TIMEOUT_SECONDS`, `LOG_*`, `API_V1_PREFIX` — arrive as written. |

`environment:` outranks `env_file:`, so the explicit overrides still win. Only a handful of
values are literals, and each one is deliberate: the container-side port numbers, the
healthchecks, the start commands, and the values that cannot survive into a container —
`POSTGRES_HOST: postgres`, `NEXUS_HOST: 0.0.0.0`, `NEXUS_PORT: 8000`, and the in-network
`VITE_API_BASE_URL` / `VITE_DEV_PROXY_TARGET` values the Vite proxy depends on. Nothing else
in the file is hardcoded. (`$$VAR` is Compose's escape for a literal `$VAR`, which is how the
healthchecks reach `POSTGRES_USER` inside the container.)

`DATABASE_URL` is the one value Compose actively clears: it is set to the empty string so
that the backend assembles the DSN from `POSTGRES_*` via
`Settings.sqlalchemy_database_uri`, with the host as `postgres` and the user and password
percent-encoded. The URL in `.env` points at `127.0.0.1`, which does not resolve inside a
container. If you would rather set a full `DATABASE_URL` of your own for the Compose stack,
remember to percent-encode any reserved character (`@ : / # ? %`) in the credentials — a raw
one produces a DSN the driver cannot parse.

### Path B — local development (hot reload, your own processes)

Requires Python 3.13+, Node 20.19+ and a reachable PostgreSQL 16.

```bash
# from the repository root
python scripts/bootstrap.py
```

The bootstrap script is idempotent and stdlib-only. It verifies Python and Node, checks
the checkout layout, reports whether Docker is available, creates `.env` from
`.env.example` if it is missing, creates `backend/.venv`, installs
`backend/requirements.txt`, runs `npm install` in `frontend/`, and prints the next steps.
Use `--skip-install` to only run the checks and create `.env`.

Then, from the repository root:

```bash
backend/.venv/bin/python scripts/wait_for_db.py          # or Scripts\python.exe; = make db-wait
backend/.venv/bin/python scripts/create_test_database.py  # = make test-db
cd backend && ../backend/.venv/bin/python -m alembic upgrade head
cd .. && ./scripts/dev.sh
```

(With GNU make the first three lines are `make db-wait`, `make test-db`, `make migrate`.)

`scripts/dev.sh` starts the backend and the frontend together against an already-running
PostgreSQL and stops both on Ctrl-C. It accepts `backend`, `frontend`, `both` (default) or
`--help`. On Windows run it from Git Bash or WSL: `bash scripts/dev.sh`.

Run the processes separately if you prefer:

```bash
# terminal 1 — backend, from backend/
python run.py

# terminal 2 — frontend, from frontend/
npm run dev
```

> **Always start the backend with `python run.py` from `backend/`.** A bare
> `uvicorn app.main:app` will start on Windows but cannot reach the database: psycopg's
> async driver needs `loop.add_reader`, which asyncio's default Windows
> `ProactorEventLoop` does not provide. `run.py` selects the correct loop via
> `backend/app/core/event_loop.py`. See [Troubleshooting](#troubleshooting).

---

## Environment variables

`.env.example` in the repository root is the source of truth, and every line of it is
commented. `.env` is git-ignored and **a fresh clone has no `.env`** — the first step of
either quick start above is copying it:

```bash
cp .env.example .env              # macOS / Linux / Git Bash
Copy-Item .env.example .env       # PowerShell
```

`Settings` in `backend/app/core/config.py` also looks for `.env` in `../.env` and
`../../.env`, so the file at the repository root is found regardless of the working
directory. Every variable is case-insensitive.

### Groups

| Group | Variables |
| --- | --- |
| Application | `ENVIRONMENT`, `DEBUG`, `APP_NAME`, `APP_VERSION`, `APP_DESCRIPTION` (shown in the OpenAPI schema and the docs UI), `OPENAPI_URL`, `DOCS_URL`, `REDOC_URL`, `API_V1_PREFIX` (the prefix every versioned route is mounted under; change it and `VITE_API_BASE_URL` has to change with it) |
| Security | `SECRET_KEY`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS` |
| Backend server (read by `backend/run.py`) | `NEXUS_HOST`, `NEXUS_PORT`, `NEXUS_RELOAD` |
| CORS | `CORS_ORIGINS` (comma-separated, no trailing slashes) |
| Database | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`, `DATABASE_URL`, `TEST_DATABASE_URL`, `DB_ECHO`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, `DB_POOL_RECYCLE`, `DB_PROBE_TIMEOUT_SECONDS` |
| Logging | `LOG_LEVEL`, `LOG_JSON`, `LOG_FILE`, `LOG_REQUEST_BODY`, `SLOW_REQUEST_MS` |
| Frontend — read by the app (only `VITE_*` reaches the browser) | `VITE_API_BASE_URL` |
| Frontend — dev server only | `VITE_DEV_PROXY_TARGET` — server-side, read by `frontend/vite.config.ts`; it is never bundled into the browser build |
| Frontend — **reserved, read by no code** | `VITE_API_SERVER_URL`, `VITE_APP_NAME`, `VITE_ENABLE_COMMAND_PALETTE` — declared in `frontend/src/vite-env.d.ts` and in `.env.example`, but no module in `frontend/src` reads them. They are kept so the names stay stable for whoever wires those features up; changing them has no effect today. |
| Docker Compose | `BIND_HOST` (see below), `POSTGRES_CONTAINER_NAME`, `POSTGRES_VOLUME_NAME`, `BACKEND_CONTAINER_NAME`, `FRONTEND_CONTAINER_NAME` |

`BIND_HOST` (default `127.0.0.1`) prefixes every published port mapping in
`docker-compose.yml` — the database, the API and the Vite dev server. The default keeps all
three reachable from this machine alone. Widening it to `0.0.0.0` or a LAN address
publishes them to every host on the network, in front of the default `nexus`/`nexus`
credentials, a placeholder `SECRET_KEY` and `DEBUG=true`. Only do that on a network you
trust as much as the machine itself.

`DB_PROBE_TIMEOUT_SECONDS` (default 3) is the wall-clock budget for the `SELECT 1` health
probe in `backend/app/db/session.py`. `DB_POOL_TIMEOUT` only bounds the wait for a free
connection, not the TCP handshake behind it, so a filtered port or a wedged server would
otherwise stall the probe until the OS TCP timeout.

One caveat on `VITE_DEV_PROXY_TARGET`: Vite resolves `.env` files relative to its own root,
which is `frontend/`, not the repository root. On the host the default
`http://localhost:8000` therefore applies and that is the correct value anyway; it is
Compose — which injects the variable straight into the frontend container's environment —
that moves the proxy to `http://backend:8000` inside the network.

### The handful a newcomer will actually change

| Variable | Why |
| --- | --- |
| `SECRET_KEY` | Signs every JWT. The `.env.example` value is a placeholder. Generate a real one with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Must match the server you actually run. `docker-compose.yml` passes them to the image; a local PostgreSQL uses your existing role. |
| `POSTGRES_HOST` / `POSTGRES_PORT` | Keep `127.0.0.1`, not `localhost` — see below. Move `POSTGRES_PORT` if a native server already owns 5432. |
| `DATABASE_URL` | Overrides the assembled URL entirely. Empty it out and `Settings.sqlalchemy_database_uri` builds the DSN from `POSTGRES_*`, percent-encoding the user and password. Compose clears it for you, because the value in `.env` points at `127.0.0.1`, which does not resolve inside the container. |
| `NEXUS_RELOAD` | `true` for local development, `false` anywhere else. Compose forces it to `false`. |

### `SECRET_KEY` in production

`app/core/config.py` refuses to start a process with `ENVIRONMENT=production` when
`SECRET_KEY` is still `dev-insecure-change-me`, and also refuses `DEBUG=true` in
production. Generate a real key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### Why `127.0.0.1` and not `localhost`

On Windows `localhost` resolves to `::1` first, and psycopg's async driver only speaks
IPv4. With `localhost` the application hangs instead of failing fast. Use `127.0.0.1` in
`DATABASE_URL`, `TEST_DATABASE_URL` and `POSTGRES_HOST`. The helper scripts rewrite
`localhost` to `127.0.0.1` for you; the application does not, because it should never
have to.

---

## Development commands

### Make targets

`make` is a thin wrapper: each target runs a real command, and the logic lives in the
scripts and npm scripts. `make` with no argument lists them.

| Target | Effect |
| --- | --- |
| `make help` | List every target with its one-line description |
| `make install` | Run `scripts/bootstrap.py` (venv, `.env`, `npm install`) |
| `make bootstrap` | Alias for `make install` |
| `make up` | `docker compose up -d --build`, then print the frontend and docs URLs |
| `make down` | `docker compose down` (keeps the volume) |
| `make logs` | `docker compose logs -f` |
| `make migrate` | `alembic upgrade head` |
| `make migrate-down` | `alembic downgrade -1` |
| `make revision m="add widgets table"` | `alembic revision -m "<message>"` |
| `make db-wait` | `scripts/wait_for_db.py --timeout $(TIMEOUT)` — block until PostgreSQL accepts connections. `make db-wait TIMEOUT=120` to wait longer. |
| `make test-db` | `scripts/create_test_database.py` — create the `nexus_test` database. Never drops anything by default; pass `TEST_DB_FLAGS=--drop` to rebuild it, which is destructive. |
| `make test` | `test-backend` then `test-frontend` |
| `make test-backend` | `pytest` in `backend/` (the 43 `integration` tests included — they need PostgreSQL) |
| `make test-frontend` | `npm run test` in `frontend/` |
| `make lint` | ruff check, ruff format --check, eslint, tsc -b |
| `make backend` | `scripts/dev.sh backend` |
| `make frontend` | `scripts/dev.sh frontend` |
| `make clean` | Remove caches, `frontend/dist`, `frontend/coverage`, `*.tsbuildinfo` |

`db-wait` and `test-db` exist so the two helper scripts no longer have to be invoked by
hand; `scripts/dev.sh` already waits for the database itself, but only for 15 seconds.

The interpreter is detected automatically. `PY` probes for
`backend/.venv/Scripts/python.exe` first and falls back to `backend/.venv/bin/python`, so
the same Makefile works on a Windows virtualenv and a POSIX one. Override it only when you
want a different interpreter — the path is always relative to the repository root:

```bash
make test-backend PY=backend/.venv/Scripts/python.exe
make migrate PY=/usr/bin/python3.13
```

`NPM` is overridable the same way (`make test-frontend NPM=pnpm`).

### Raw commands

Backend — run from `backend/`:

```bash
python run.py                                   # dev server, honours NEXUS_HOST/PORT/RELOAD
python -m pytest                                # 188 tests: 145 plus 43 integration (need PostgreSQL)
python -m pytest -m "not integration"           # 145 tests, no database needed
python -m ruff check .
python -m ruff format --check .
python -m alembic upgrade head                  # apply migrations
python -m alembic downgrade -1                  # roll back one migration
python -m alembic revision -m "add widgets table"
python -m alembic revision --autogenerate -m "add widgets table"
python -m alembic history --verbose
```

Use `backend/.venv/bin/python` (or `backend\.venv\Scripts\python.exe`) unless your
environment already has the dependencies installed.

Frontend — run from `frontend/`:

```bash
npm run dev            # Vite dev server on :5173
npm run build          # tsc -b && vite build
npm run preview        # serve dist/ on :4173
npm test               # vitest run (30 tests in 10 files)
npm run test:watch     # vitest
npm run test:coverage  # vitest run --coverage
npm run lint           # eslint .
npm run typecheck      # tsc -b
```

Scripts — run from the repository root:

```bash
python scripts/bootstrap.py [--skip-install]
python scripts/wait_for_db.py [--url URL] [--timeout 60] [--interval 1] [--quiet]
python scripts/create_test_database.py [--url URL] [--drop]
python scripts/verify_compose.py [--strict]    # needs PyYAML; not a project dependency
bash scripts/dev.sh [both|backend|frontend]
```

### What the scripts actually do

| Script | Behaviour |
| --- | --- |
| `scripts/bootstrap.py` | Stdlib only. Checks Python ≥ 3.13, Node ≥ 20 and npm, verifies the checkout, reports Docker availability, creates `.env` from `.env.example` (never overwriting an existing one), creates or reuses `backend/.venv`, `pip install -r backend/requirements.txt`, `npm install`, prints next steps. |
| `scripts/wait_for_db.py` | Polls `SELECT 1` until the configured database accepts connections. Default timeout 60 s, interval 1 s, per-attempt connect timeout 5 s. Turns libpq's error into a remedy (role missing / database missing / bad password / nothing listening). Passwords are redacted from every message, including credentials libpq echoes back inside errors. |
| `scripts/create_test_database.py` | Creates the `nexus_test` database if missing, connecting to the `postgres` maintenance database in autocommit mode. Then tries to enable `pg_trgm` and `unaccent`; a missing contrib module is a warning, not an error. `--drop` recreates it (destructive). |
| `scripts/dev.sh` | Starts `python run.py` and `npm run dev` together, waits for the database for up to 15 s (non-fatal), and stops both on Ctrl-C. If either server exits, it takes the other one down too. Uses `taskkill /T` on Windows because `npm run dev` orphans `node.exe` and `esbuild.exe`. |
| `scripts/verify_compose.py` | Parses `docker-compose.yml` with PyYAML and checks: Compose v2 syntax (no `version:` key), project `name: nexus`, exactly the three expected services, every `build.context` is a real directory containing the Dockerfile, every bind-mounted host path exists, and every `${VAR}` is documented in `.env.example` (14 variables at the time of writing). `--strict` additionally warns about `.env.example` entries compose never uses. Static only — it cannot tell you that `docker compose up` works. |
| `scripts/_common.py` | Shared helpers: `.env` parsing (hand-written so `bootstrap.py` works on a bare interpreter), URL resolution/redaction, venv interpreter discovery, lazy `psycopg` import. Not a command. |

---

## API documentation

| What | URL |
| --- | --- |
| Swagger UI | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |
| OpenAPI JSON | http://localhost:8000/openapi.json |
| Service metadata | http://localhost:8000/ |
| Liveness (no database) | http://localhost:8000/health |
| Detailed health | http://localhost:8000/api/v1/health |

Current endpoints:

| Method | Path | Auth | Success |
| --- | --- | --- | --- |
| `GET` | `/` | no | 200, service metadata and endpoint links |
| `GET` | `/health` | no | 200 `{"status":"ok"}` — never touches the database |
| `GET` | `/api/v1/health` | no | 200, app/version/environment/database status + latency/uptime/timestamp |
| `POST` | `/api/v1/auth/register` | no | 201, `UserRead` |
| `POST` | `/api/v1/auth/login` | no | 200, `TokenPair` |
| `POST` | `/api/v1/auth/refresh` | no | 200, `TokenPair` (the presented refresh token is retired) |
| `POST` | `/api/v1/auth/logout` | optional | 204 no content |
| `GET` | `/api/v1/auth/me` | bearer | 200, `UserRead` |

The full contract — error codes, request ids, pagination, and the rules every future
endpoint must follow — is in [`docs/api-conventions.md`](docs/api-conventions.md).

---

## Troubleshooting

### The database is unreachable

**Symptom.** `/health` is green, but `/api/v1/health` reports
`"status": "degraded"` with `"database": {"status": "unavailable"}`, and auth calls fail.
That is by design: `/health` is liveness and must stay green while PostgreSQL is down, so
a dependency failure does not trigger a restart loop.

**Diagnose.**

```bash
# from the repository root
backend/.venv/bin/python scripts/wait_for_db.py --timeout 10
```

The script classifies the failure and tells you what to change. The common causes are a
PostgreSQL that is not running, `POSTGRES_USER`/`POSTGRES_PASSWORD` in `.env` not
matching the server's own role, a `DATABASE_URL` pointing at `localhost` instead of
`127.0.0.1`, or a schema that has never been migrated.

The endpoint itself will not hang while it works this out: the probe in
`backend/app/db/session.py` wraps its `SELECT 1` in `asyncio.timeout` with
`DB_PROBE_TIMEOUT_SECONDS` (3 s by default), so a filtered port or a wedged server is
reported as `"status": "degraded"` — a real answer — instead of blocking the response until
the OS TCP timeout gives up. `DB_POOL_TIMEOUT` does not help here: it bounds waiting for a
free connection from the pool, not the handshake behind it. If your probes fail at exactly
`DB_PROBE_TIMEOUT_SECONDS`, the budget is the right thing to raise in `.env`.

**Fix, in order:**

```bash
docker compose up -d postgres                     # or start your local PostgreSQL
cd backend && ../backend/.venv/bin/python -m alembic upgrade head
```

Confirm with `http://localhost:8000/api/v1/health` — `database.status` must read
`connected`.

### Port already in use

| Port | Held by | Fix |
| --- | --- | --- |
| 5432 | a native PostgreSQL, or an older NEXUS stack | move the native one (`postgresql-<n> start`), or set `POSTGRES_PORT` in `.env` — it is the host side of the published mapping |
| 8000 | a previous backend, or `make up` still running | `docker compose down`, or set `NEXUS_PORT` in `.env` for a host run. The Compose mapping is a literal `8000:8000`, so change the mapping or stop the stack |
| 5173 | another Vite server | Vite runs with `strictPort: true`, so it exits rather than picking a different port — stop the other process |
| 4173 | `npm run preview` already running | stop it, or change `preview.port` in `frontend/vite.config.ts` |

### `InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode`

You started the backend with `uvicorn app.main:app`. That works on Linux and macOS and
starts on Windows, but psycopg's async driver cannot run on asyncio's Windows default
`ProactorEventLoop`, so every database operation fails.

The loop is selected in exactly one place, `backend/app/core/event_loop.py`, and applied
by `backend/run.py`, by `migrations/env.py` and by `tests/conftest.py`. Start the server
with:

```bash
cd backend && python run.py
```

### The application hangs on startup instead of failing

`DATABASE_URL` or `POSTGRES_HOST` contains `localhost`. On Windows that resolves to
`::1`, which psycopg cannot connect to, so the TCP connect never fails — it just waits.
Replace `localhost` with `127.0.0.1`.

### `database "nexus_test" does not exist`

The PostgreSQL image ships only `postgres`, `template0` and `template1`. The test
database is created for you — by `tests/conftest.py` when the suite runs, or explicitly:

```bash
# from the repository root
backend/.venv/bin/python scripts/create_test_database.py   # or: make test-db
```

The script only creates what is missing. `--drop` (or `make test-db TEST_DB_FLAGS=--drop`)
recreates the database and destroys whatever it holds, so it is never the default.

If `TEST_DATABASE_URL` points at the same database as `DATABASE_URL`, the script refuses
to run: the suite truncates tables, and the development database must never be that
database.

### `Settings` fails validation

`SECRET_KEY must be set to a strong random value when ENVIRONMENT=production.` — or
`DEBUG must be false when ENVIRONMENT=production.` Both are raised from
`app/core/config.py` at import time.

### Hot reload is not working

`NEXUS_RELOAD` defaults to `false` in `run.py`, so set `NEXUS_RELOAD=true` in `.env` for
local development. The Compose backend forces it to `false` on purpose.

### `docker compose up` fails before anything starts

Run the static check, from the repository root:

```bash
backend/.venv/bin/python -m pip install pyyaml     # PyYAML is deliberately not a dependency
backend/.venv/bin/python scripts/verify_compose.py
```

It reports the three services and their build contexts, the bind-mounted host paths, and
that the 14 `${VAR}` the compose file interpolates are all documented in `.env.example`.
Add `--strict` to also list `.env.example` entries compose never uses. It exits non-zero on
any failure. None of that tells you whether the images build or the stack comes up.

Note that `docker-compose.yml` was authored on a machine without Docker, and it has never
been executed by `docker compose`. The YAML is structurally validated by that script; treat
the first real run as untested and go straight to `docker compose logs -f`.

---

## Roadmap

Phase numbers are the ones recorded in `frontend/src/features/modules/catalog.ts`.

| Phase | Module | Adds |
| --- | --- | --- |
| 1 | Foundation (done) | Repo, stack, auth, migrations, design system, health, docs, tests |
| 1 | Dashboard, Settings (live) | Service health card, theme and session preferences |
| 2 | Projects, Tasks | The execution layer: outcomes, projects, tasks, triage views |
| 3 | Planner | Weekly capacity, time blocks, focus log |
| 4 | Knowledge, Search | Linked notes, retrieval index, unified search |
| 5 | Analytics | Traceable metrics derived from real records |
| 6 | Developer | Local git repository analysis, work attribution |
| 7 | Learning | Tracks, spaced review, applied evidence |
| 8 | Career | Goals, evidence, opportunity log |
| 9 | AI Assistant | Grounded local-LLM answers via Ollama, proposed actions |
| 10 | Experiments | Hypothesis, bounded scope, keep-or-kill verdict |

Behind those modules sit infrastructure seams that are described in the extension-roadmap
section of [`docs/architecture.md`](docs/architecture.md): Redis (replacing the
in-process token revocation store), background workers, an ML training pipeline, a local
model registry, Ollama-backed local LLM features, and git repository analysis. None of
them exists in Phase 1.

---

## Further documentation

| Document | Contents |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | Layering, request lifecycle, auth design, configuration, logging, database strategy, extension roadmap, decisions and rationale |
| [`docs/development.md`](docs/development.md) | Clean-machine setup, the daily loop, migrations, adding an endpoint, page or test, testing conventions, design-system and code conventions, the pre-pull-request checklist, and the verified baseline (including what was *not* run) |
| [`docs/api-conventions.md`](docs/api-conventions.md) | Base URL and versioning, health endpoints, authentication, the error envelope and its full code table, request-id correlation, pagination, the checklist every endpoint must satisfy |