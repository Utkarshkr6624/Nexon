# NEXUS — Architecture

Reference for the Phase 1 technical foundation. This document explains *how the
system is put together and why*, and defers to [`../README.md`](../README.md)
for installation, day-to-day commands and troubleshooting. Everything below was
read from the code; where a number appears it came from the repository, not from
intention. Where something could **not** be exercised here — which currently means
anything needing a live PostgreSQL or a running container — that is said
explicitly rather than glossed: see [What has not been run](#what-has-not-been-run).

**Scope.** Phase 1 delivers the platform skeleton and one working vertical slice
(auth + `users` table). Projects, Tasks, Planner, Knowledge, Search, Analytics,
Developer, Learning, Career, AI Assistant and Experiments are placeholder pages.
They are described here only as *seams* — see
[Extension roadmap](#extension-roadmap).

---

## Table of contents

| Section | Contents |
| --- | --- |
| [1. System context](#1-system-context) | Processes, ports, topology, configuration flow |
| [2. Backend layering](#2-backend-layering) | The dependency rule and what each layer may know |
| [3. Request lifecycle](#3-request-lifecycle) | What happens to a request, in order |
| [4. Error contract](#4-error-contract) | The one envelope and the code table |
| [5. Health and readiness](#5-health-and-readiness) | Liveness vs. the database probe |
| [6. Authentication and authorisation](#6-authentication-and-authorisation) | Tokens, rotation, revocation, dependency ladder |
| [7. Persistence](#7-persistence) | Engine, pool, models, migrations, test database |
| [8. Configuration model](#8-configuration-model) | One `Settings` object, derived URLs, production guards |
| [9. Observability](#9-observability) | Logging sinks, redaction, correlation |
| [10. Event loop and platform constraints](#10-event-loop-and-platform-constraints) | The psycopg/Windows problem, solved once |
| [11. Process model](#11-process-model) | Why one worker, and what that forbids |
| [12. Frontend architecture](#12-frontend-architecture) | Providers, routing, registry, data access, build |
| [13. Testing architecture](#13-testing-architecture) | Fixtures, markers, what is and is not covered or verified |
| [14. Container topology](#14-container-topology) | Compose services and why they are wired that way |
| [15. Extension roadmap](#15-extension-roadmap) | Named seams for later phases |
| [16. Design decisions](#16-design-decisions) | Decision → rationale → cost |

---

## 1. System context

Three processes, all on one machine. There is no external service, no cloud
account, no third-party API.

| Process | Host port | Started by | Notes |
| --- | --- | --- | --- |
| PostgreSQL 16 (`postgres:16-alpine`) | 5432 | Compose, or your own server | named volume `nexus_pgdata` |
| FastAPI (uvicorn) | 8000 | `backend/run.py` | `NEXUS_HOST` / `NEXUS_PORT` / `NEXUS_RELOAD` |
| Vite dev server | 5173 | `npm run dev` | `strictPort: true`; preview server on 4173 |

```text
  browser ──5173──► Vite dev server ──proxy /api, /health──► FastAPI ──5432──► PostgreSQL
                                                  (8000)
```

Two transport topologies coexist, and the difference matters:

| Mode | `VITE_API_BASE_URL` | What the browser calls | Where `/api/v1` resolves |
| --- | --- | --- | --- |
| Host development | `http://localhost:8000/api/v1` (from `.env.example`) | the backend cross-origin | the API, CORS applies |
| Compose | `/api/v1` (set in `docker-compose.yml`) | same-origin | Vite's proxy → `VITE_DEV_PROXY_TARGET` |

`VITE_DEV_PROXY_TARGET` defaults to `http://localhost:8000` and is set to
`http://backend:8000` by Compose, because inside the compose network
`localhost:8000` would be the frontend container itself. The `server.proxy` block
in `frontend/vite.config.ts` also covers `/health`, which sits outside the version
prefix, and `preview` is given the same routes so a previewed production bundle
behaves like the reverse proxy that will eventually front the API.

### Configuration flow

One `.env` at the repository root feeds both processes. `Settings`
(`backend/app/core/config.py`) searches `.env`, `../.env`, `../../.env`, so the
file is found regardless of the working directory. `docker-compose.yml` reads the
same file directly and injects `${VAR:-default}` values per service. There is no
second configuration channel: every value that shapes behaviour is an environment
variable listed in [`.env.example`](../.env.example).

**The `127.0.0.1` rule.** Database URLs use `127.0.0.1`, never `localhost`. On
Windows `localhost` resolves to `::1` first and psycopg's async driver is IPv4
only, so the TCP connect neither succeeds nor fails — it hangs. The application
does not rewrite the host for you (`Settings.postgres_host` defaults to
`127.0.0.1`, and that is the end of it); the helper scripts in `scripts/` do the
rewrite, because a script that hangs is useless for diagnosis.

---

## 2. Backend layering

Four layers, one dependency rule: **dependencies point downward only.**

| Layer | Package | Knows about | Must not know about |
| --- | --- | --- | --- |
| HTTP | `app/api/v1/*.py`, `app/api/deps.py` | FastAPI, schemas, services | SQL, ORM internals |
| Domain | `app/services/*.py` | schemas, repositories, `app.core` | FastAPI (never imported) |
| Data | `app/repositories/*.py` | SQLAlchemy, ORM models | HTTP, error semantics |
| Infrastructure | `app/core/*`, `app/db/*`, `app/models/*` | everything below it | services, routers |

The rule is enforced by intent and visible in the code: `app/services/auth_service.py`
states in its module docstring that it never imports FastAPI, and
`app/repositories/user.py` never raises a domain error — an `IntegrityError` is
allowed to propagate so the service can translate it into `409 conflict`.

That translation happens in the service layer on **both** write paths, which is
what makes "one translation point" true rather than aspirational:

| Path | Pre-check | Race guard |
| --- | --- | --- |
| `AuthService.register` | `exists_by_email` → `ConflictError` | `IntegrityError` → the same `ConflictError`, chained |
| `UserService.update` (email) | `get_by_email`, ignoring the caller's own row → `ConflictError` | `IntegrityError` on commit → the same `ConflictError`, chained |

Without the second column, a concurrent writer would win the race and the loser
would surface a driver error as a 500. `UserService.update` normalises the e-mail
itself before the lookup, so the value the uniqueness check compares against is
the value the column will hold, whichever caller is writing.

`UserService` is wired into `app/api/deps.py` (`get_user_service`) but has **no
router yet** — there is no `users` endpoint, and `UserService.update` has no test.
It is a seam, and is listed as one under [Extension roadmap](#15-extension-roadmap).

Two dependency providers split the HTTP wiring:

| Module | Responsibility |
| --- | --- |
| `app/core/deps.py` | Identity only: bearer scheme, current user, optional user, superuser, session→repository. Talks to the repository directly and imports no service, which keeps the graph acyclic. |
| `app/api/deps.py` | Layering on top: session → repository → service, plus the revocation check that turns a cryptographically valid JWT into a rejected one. Re-exports the `core` names so routers have one import site. |

Schemas (`app/schemas/`) sit on the HTTP boundary and are the only place Pydantic
validation runs. `hashed_password` is absent from every model that can leave the
process.

---

## 3. Request lifecycle

Registered in `backend/app/main.py`; the order below is the execution order,
outermost first.

| # | Layer | Installed by | Behaviour |
| --- | --- | --- | --- |
| 1 | `RequestContextMiddleware` | `add_request_context_middleware` → `_install_outermost` | Binds the correlation id, stamps `X-Request-ID` onto the outgoing `http.response.start`, times the request, emits the access line. |
| 2 | `BodyCaptureMiddleware` | `app.add_middleware` — only when `LOG_REQUEST_BODY=true` | Buffers the ASGI body messages, publishes a capped copy on `scope["state"]`, replays them verbatim. |
| 3 | `CORSMiddleware` | `app.add_middleware`, first | Preflight and header work. `X-Request-ID` is in `expose_headers`; credentials are allowed. |
| 4 | `ServerErrorMiddleware` | Starlette, in `build_middleware_stack` | Catches anything escaping layer 5 and renders it through the catch-all handler. |
| 5 | `ExceptionMiddleware` → router → dependencies | Starlette, in `build_middleware_stack` | Registered handlers, routing, `app/api/v1/router.py` (`health` + `auth`), mounted at `settings.api_v1_prefix`. |

`add_middleware` inserts outermost-last, which is why body capture ends up
*outside* CORS: `create_app` adds CORS first and then, from inside
`add_request_context_middleware`, adds body capture. Both still sit below
`RequestContextMiddleware`.

`RequestContextMiddleware` is **above** `ServerErrorMiddleware`, and that is
load-bearing rather than incidental. Starlette builds the user middleware stack
*inside* `ServerErrorMiddleware`, so anything registered with `add_middleware`
sits below the layer that renders an unhandled exception into a 500 — and never
sees that response. Registered the ordinary way, the middleware could set
`X-Request-ID` on a normal response only, and every 500 would have gone out with
no correlation header while the error *body* still carried a `request_id`. That
split is worse than neither: a bug report quoting the header would name nothing,
and the frontend's error correlation would fail on precisely the requests that
need it.

The fix has two halves, both in `backend/app/core/middleware.py`:

| Mechanism | What it does |
| --- | --- |
| `_install_outermost` | Overrides the app's `build_middleware_stack` so the whole stack is wrapped by `RequestContextMiddleware`. The build is still deferred to the first request, so handlers and routes registered after the call are still part of it. |
| A pure-ASGI `__call__` | The header is written onto the `http.response.start` message with `MutableHeaders` rather than onto a response object, so it is present whatever produced that response — including a 500 rendered below it. |

Two consequences follow for the middleware itself. It is not a
`BaseHTTPMiddleware` subclass, so `request.state` and response-header work are
done by hand rather than delegated. And it recovers the status code from the
same message it stamps, defaulting to 500 when the request fails before a
response starts — so a connection that dies mid-handler still produces one
access line, at ERROR.

Inside `__call__` the correlation id is bound with `set_request_id()` before
anything downstream can log, the token is reset in the `finally` after the access
line is emitted, and `request.state.request_id` is stamped alongside it as a
fallback.

Two things about the stack below it are unchanged by any of this. Routers
translate exceptions into nothing at all — they pick a success status and return,
and every error surfaces as an exception rendered centrally. And exactly one
access line is produced per request, in the middleware's `finally`, at ERROR for
5xx, WARNING for 4xx or for a duration at or above `SLOW_REQUEST_MS`, INFO
otherwise; uvicorn's own access log is disabled in `configure_logging` so the two
cannot double-count.

### Request id

| Property | Behaviour |
| --- | --- |
| Source | `X-Request-ID`, then `X-Correlation-ID`, then `X-Trace-ID`, else a fresh UUID4 |
| Trust | Inbound values are attacker-controlled: stripped and capped at 128 characters so a log line cannot be forged with newlines |
| Propagation | Bound to a `contextvars.ContextVar`, attached to every log record by `ContextFilter`, stamped onto the response-start message, embedded in every error body |
| Catch-all path | The catch-all handler runs *inside* this middleware's call, so the contextvar is still bound and is the primary source. `resolve_request_id()` falls back to `request.state.request_id` as a safety net, not as the main path. |

Because the header and the body are produced by different layers from the same
id, they are guaranteed to agree — on **every** status, 500 included. The
`assert_error_envelope` fixture asserts that equality, and
`tests/test_error_handling.py` repeats it on the 5xx path, along with the
caller-supplied, over-long and newline-forged inbound id cases.

---

## 4. Error contract

Every non-2xx response has the same shape (`backend/app/core/exceptions.py`):

```json
{
  "error": {
    "code": "not_found",
    "message": "The requested resource was not found.",
    "details": null,
    "request_id": "0f1e..."
  }
}
```

[`api-conventions.md`](api-conventions.md#the-error-envelope) owns this contract
as a wire format and is the document to read for it. What follows is the
implementation of it inside this backend.

| Field | Contract |
| --- | --- |
| `code` | Stable snake_case. Branch on this, never on `message`. |
| `message` | Always safe to render to a user. No stack traces, SQL, driver names or internal paths. |
| `details` | Machine-only. `null` unless the error carries structured data (for `validation_error`, `{"errors": [...]}` with `field` / `message` / `type`). Always `null` on a 5xx. |
| `request_id` | Equal to the `X-Request-ID` response header on **every** status, 500 included — the header and the body are stamped from one id by the middleware above. `tests/test_error_handling.py` asserts the equality on the 5xx path; `assert_error_envelope` asserts it everywhere. |

| `code` | HTTP | Raised by |
| --- | --- | --- |
| `validation_error` | 422 | `RequestValidationError` handler, `ValidationError` |
| `bad_request` | 400, **and any 4xx with no explicit mapping** (413, 415, 402, …) | `StarletteHTTPException` mapping |
| `unauthorized` | 401 | `UnauthorizedError` (adds `WWW-Authenticate: Bearer`) |
| `forbidden` | 403 | `ForbiddenError` |
| `not_found` | 404 | `NotFoundError` |
| `method_not_allowed` | 405 | `StarletteHTTPException` mapping |
| `conflict` | 409 | `ConflictError` |
| `rate_limited` | 429 | `StarletteHTTPException` mapping (code reserved; no limiter is implemented) |
| `internal_error` | any 5xx | catch-all handler, and any `StarletteHTTPException` at 5xx — traceback and detail stay in the log |

Two rules in that table are worth stating separately, because both are deliberate
and neither is obvious from the envelope.

**A 5xx never echoes a caller-supplied `detail`.** `StarletteHTTPException` lets
a handler choose any `detail` string, and a 5xx raised that way would have put
that text on the wire. It is text the application did not author for a client —
a framework or dependency string, or application prose about the failure — and
it can carry SQL, module paths or credentials. So `_handle_http_exception`
replaces the message with the single constant `_INTERNAL_ERROR_MESSAGE`
("An internal server error occurred.") and forces `details` to `None` for any
status at or above 500; the original detail is logged as `http_exception` at
ERROR and stays reachable by `request_id`. `tests/test_error_handling.py` drives
a route that raises a 5xx carrying a constraint name and a SELECT, then asserts
the string is absent from the body and present in the log.

**`internal_error` is reserved for 5xx.** An unmapped status used to fall through
to it, which meant a 413 or a 415 — the caller's fault, entirely — was reported
as a server fault. The frontend branches on `code`, so that would send every
oversized upload and every wrong content type down the "something is broken on
our side" path. `_status_code_to_code` now returns `bad_request` for any 4xx
with no explicit mapping and reserves `internal_error` for 5xx;
`tests/test_errors.py::test_error_codes_are_stable_snake_case` asserts the
invariant `not code.startswith("internal") or status >= 500`.

The mapping from HTTP status to `code` lives in one dict
(`_STATUS_CODE_TO_ERROR_CODE`), so a handler never invents a code. Domain errors
subclass `NexusError`, which carries `code`, `status_code` and a default
message; instances may override the message and attach `details`.

The frontend consumes this envelope in `frontend/src/lib/api-client.ts`: every
failure path throws a single `ApiError` carrying `status`, `code`, `details` and
`requestId`, with a status-derived fallback for responses that are not in the
envelope shape (a proxy 502, for instance).

---

## 5. Health and readiness

Two endpoints with deliberately different contracts.

| Endpoint | Touches the database | Body | Contract |
| --- | --- | --- | --- |
| `GET /health` | No | `{"status": "ok"}` | Liveness. Must stay green while PostgreSQL is down, otherwise a dependency failure triggers a restart loop instead of a page. |
| `GET /api/v1/health` | Yes — timed `SELECT 1` | `{status, app, version, environment, database:{status, latency_ms}, uptime_seconds, timestamp}` | Readiness. Returns **200** with `status: "degraded"` and `database.status: "unavailable"` when the probe fails — the endpoint reporting degradation is itself healthy. |
| `GET /` | No | service, version, environment, `api_version`, endpoint links | Meta. |

Uptime is computed from `time.monotonic()`, so it is unaffected by wall-clock
changes. The probe also runs once at startup (`app/main.py:_lifespan`) and is
logged as `database_probe` at INFO when it succeeds, WARNING when it does not —
startup never fails because the database is down. The lifespan reads
`app.state.settings` — the object `create_app(settings=...)` was given — so a
caller that supplies its own settings gets those settings for logging and for the
probe, not the global singleton; the CORS origins, docs URLs and API prefix were
already built from it. The probe itself is additionally wrapped in a `try`, so a
future failure mode degrades the log line rather than blocking startup.

The probe's own timeout is described under [Persistence](#7-persistence); it is
the reason a wedged server produces `degraded` quickly instead of hanging.

The container healthchecks use `/health`, not the detailed endpoint, for exactly
the reason above — `docker-compose.yml`'s `backend` healthcheck and the
`HEALTHCHECK` line in `backend/Dockerfile` both curl `127.0.0.1:$NEXUS_PORT/health`.

The Dashboard health card on the frontend polls `/api/v1/health` every 30 s with
a 5 s `staleTime` (`frontend/src/features/health/use-health.ts`). It is the only
live data dependency in the shell.

---

## 6. Authentication and authorisation

### Tokens

HS256 JWTs signed with `SECRET_KEY`. Lifetimes come from
`ACCESS_TOKEN_EXPIRE_MINUTES` (default 60) and `REFRESH_TOKEN_EXPIRE_DAYS`
(default 7). `TokenPair.expires_in` is the access lifetime in seconds.

| Claim | Set by | Purpose |
| --- | --- | --- |
| `sub` | `app/core/security.py` | user id (UUID, string-encoded) |
| `type` | `app/core/security.py` | `access` or `refresh` — the claim that stops a refresh token being replayed as a bearer credential |
| `iat`, `nbf`, `exp` | `app/core/security.py` | standard time claims |
| `jti` | `app/services/auth_service.py` | token id; the key of the revocation denylist |

`decode_token()` requires `exp`, `sub` and `type` to be present, restricts the
accepted algorithm to `settings.jwt_algorithm`, and converts *every* failure —
bad signature, wrong algorithm, expiry, malformed input, wrong `type` — into a
single `UnauthorizedError`. Callers never have to reason about which PyJWT
exception occurred.

Passwords use bcrypt at cost 12, chosen above the library default because ~250 ms
per hash on commodity hardware is the intended trade against brute force. Input is
truncated to bcrypt's 72-byte ceiling rather than allowed to raise.

### Flows

| Flow | Endpoint | Rules |
| --- | --- | --- |
| Register | `POST /api/v1/auth/register` | Duplicate e-mail → `409`, checked twice: a pre-check for the common case and an `IntegrityError` catch for the concurrent-registration race. |
| Login | `POST /api/v1/auth/login` | Every failed check returns the same message *and takes comparable time*, so probing cannot distinguish "unknown email" from "wrong password" — see below. On success `last_login_at` is stamped. Inactive accounts are rejected separately. |
| Refresh | `POST /api/v1/auth/refresh` | Single-use rotation: the presented token is revoked before the new pair is issued, so a replay fails with 401. |
| Logout | `POST /api/v1/auth/logout` | Optional body and/or bearer token; revokes whatever is parseable and returns 204. Unparseable tokens are ignored — logout must never fail. |
| Me | `GET /api/v1/auth/me` | Resolves the caller through `AuthenticatedUser`, which is the only place the revocation denylist is consulted. |

`AuthenticatedUser` layers on top of `CurrentUser`: a JWT remains cryptographically
valid after logout, and `get_authenticated_user` is what makes the denylist
observable. Endpoints that need real logout semantics must depend on
`AuthenticatedUser`, not `CurrentUser`.

### Account enumeration

The shared error message hides whether an address is registered. That is only
half of it — the response *clock* leaks the same fact just as well, and a shared
string does nothing about a 1 ms answer for an unknown address against a ~250 ms
answer for a known one.

`AuthService.authenticate` therefore always runs a bcrypt verify. When the
lookup misses, it verifies the submitted password against `_decoy_hash()` — a
bcrypt hash of a random value, computed once per process by a `@cache`d helper so
the cost stays off the import path, and discarded so no submitted password can
ever match it. The result is then folded into the one shared branch:

```python
user = await self.repository.get_by_email(str(data.email))
stored_hash = user.hashed_password if user is not None else _decoy_hash()
password_ok = verify_password(data.password, stored_hash)
if user is None or not password_ok:
    raise UnauthorizedError(_INVALID_CREDENTIALS)
```

Both failures then cost one bcrypt verify plus one index lookup, and say the same
thing.

### Revocation store

```python
class RevocationStore:          # backend/app/services/auth_service.py
    async def revoke(self, jti: str, expires_at: datetime) -> None
    async def is_revoked(self, jti: str) -> bool
```

An in-process dictionary keyed by `jti`, guarded by an `asyncio.Lock`, purged of
entries whose token would have expired anyway. Both entry points purge, not just
the lookup: a logout-only workload never calls `is_revoked`, so purging on read
alone would let the bound depend on authenticated traffic continuing. Purging on
`revoke()` too means the dictionary only ever holds revocations that are still
live. The interface is deliberately narrow so a later phase can back it with Redis
without touching the auth service. It is a module singleton
(`get_revocation_store()`) and therefore **per process**; see
[Process model](#11-process-model).

---

## 7. Persistence

### Engine and session

| Property | Value |
| --- | --- |
| Driver | `postgresql+psycopg` (psycopg 3, binary wheel) |
| Engine | One per process, created lazily by `get_engine()`, disposed in the lifespan `finally` — and only if one was ever built (see below) |
| Pool | `QueuePool`, `pool_pre_ping=True` — a stale connection is detected on checkout rather than at query time |
| Tuning | `DB_POOL_SIZE` (5), `DB_MAX_OVERFLOW` (10), `DB_POOL_TIMEOUT` (30 s), `DB_POOL_RECYCLE` (1800 s) |
| Probe | `check_database_connection()` is bounded by `DB_PROBE_TIMEOUT_SECONDS` (default 3), independently of `DB_POOL_TIMEOUT` |
| Session | `autoflush=False`, `autocommit=False`, `expire_on_commit=False` — response models read attributes after commit without a reload |
| Request scope | `get_db()` yields one session per request, rolls back on any unhandled exception, always closes |

`expire_on_commit=False` is what lets `UserRepository.create()` return a populated
instance without a second round trip; it still calls `refresh()` because
`created_at` / `updated_at` come from server defaults.

### Engine lifetime

The lifespan's shutdown half reads the module-global `db_session._engine`
directly and disposes it only when it is not `None`. Calling `get_engine()` there
instead would be wrong in the other direction: the engine is created *lazily*,
so an application that never issued a query — and an in-process client, which
never runs the lifespan's own startup path — would have an engine built from the
default settings purely so the shutdown hook could close it. Reading the global
keeps "dispose what was created" honest.

### The database health probe

`check_database_connection()` opens a connection and runs `SELECT 1` under
`asyncio.timeout(settings.db_probe_timeout_seconds)`, reporting `False` — which
the readiness endpoint renders as `database.status: "unavailable"` — on overrun
or on any exception.

`DB_POOL_TIMEOUT` would not have been enough, and the distinction is worth
stating because the two numbers sit next to each other in `.env.example` for no
apparent reason. `pool_timeout` bounds how long a caller *waits for a pooled
connection to become available* — an exhausted pool, with `pool_size` +
`max_overflow` all in use. It says nothing about how long the connection then
takes to be established. A filtered port, a TLS negotiation that never
completes, or a wedged server with the pool empty passes straight through
`pool_timeout` and blocks until the OS gives up on the TCP timeout, minutes
later. The readiness endpoint is precisely the call that must not hang: it is
what a container healthcheck and a "should I wait or give up" UI both poll.

The `finally` block closes the connection unconditionally, because a connect
aborted by the timeout never reaches `__aenter__` and so never runs `__aexit__`
to hand it back.

### Model base

`app/db/base.py` supplies the two mixins every table gets:

| Mixin | Columns | Why |
| --- | --- | --- |
| `UUIDPrimaryKeyMixin` | `id UUID PK`, default `uuid.uuid4` | Generated application-side, so the id is known before flush and no sequential volume leaks. |
| `TimestampMixin` | `created_at`, `updated_at`, `now()`, timezone-aware | Immutable creation, mutable update, both server-side defaults. |

`Base.metadata` is the single source of truth for autogenerate. `users.email`
uniqueness lives on a unique index (`ix_users_email`), because SQLAlchemy folds
`unique=True, index=True` into the index rather than emitting a separate
constraint — the migration records that explicitly with a comment.

### Migrations

| Property | Value |
| --- | --- |
| Tool | Alembic, `script_location = migrations`, `prepend_sys_path = .` |
| URL | `sqlalchemy.url` is **empty** in `alembic.ini`; `migrations/env.py` injects `get_settings().sqlalchemy_database_uri`. No credentials are tracked by git. |
| Engine | Async, `NullPool`, driven through `connection.run_sync()` because SQLAlchemy's async engine cannot run migrations directly |
| Event loop | `asyncio.run(..., loop_factory=nexus_loop_factory)` — the same factory the server and the test suite use |
| Scope | `include_object` restricts autogenerate to the `public` schema and excludes `alembic_version`, `spatial_ref_sys` |
| Rendering | `render_item` emits `postgresql.UUID(as_uuid=True)` so generated migrations state the dialect explicitly |

Current chain: one revision, `0001_initial_create_users`. Models are deliberately
**not** imported by migration files, so editing `app/models/user.py` cannot rewrite
history.

Commands, all from `backend/`:

```bash
python -m alembic upgrade head            # apply everything
python -m alembic downgrade -1            # roll back one revision
python -m alembic downgrade base          # empty the schema
python -m alembic revision -m "message"   # new, empty revision
python -m alembic check                   # autogenerate drift check
```

`alembic check` is the command for drift, and
`backend/tests/test_migrations.py::test_autogenerate_reports_no_drift` asserts
the same thing with `compare_type` and `compare_server_default` enabled, so drift
is a test failure rather than a discovery. Neither can be *run* without a live
PostgreSQL; the one-revision chain itself is checked by
`test_the_migration_chain_is_linear_and_has_a_single_head`, which parses the
version files and needs no database.

### Extensions

`docker/postgres/init/10_extensions.sql` runs on first init of an empty volume and
creates `pg_trgm` and `unaccent` — prerequisites for the Phase 4 search work
(fuzzy matching, accent-insensitive comparison). It creates nothing else: no
tables, no roles, no seed data. Alembic owns the schema.
`scripts/create_test_database.py` enables the same two extensions in `nexus_test`
so a native-PostgreSQL test run exercises the same features as the container; a
missing contrib module is a warning, not an error. The suite provisions the same
two extensions itself, in `conftest.py:_ensure_extensions`, because the script
is optional and may never have been run on a given machine — `CREATE EXTENSION
IF NOT EXISTS` is idempotent, so running both is harmless.

### Test database

The Postgres image ships only `postgres`, `template0` and `template1`.
`nexus_test` is created by `tests/conftest.py` (`_ensure_database_exists`, via an
AUTOCOMMIT connection to the `postgres` maintenance database, because
`CREATE DATABASE` cannot run inside a transaction) and by
`scripts/create_test_database.py`.

Both refuse to run if `TEST_DATABASE_URL` resolves to the application database,
and that refusal is load-bearing: `truncated_database` runs `TRUNCATE … RESTART
IDENTITY CASCADE` over every managed table before each test, so a
`TEST_DATABASE_URL` left pointing at the real database would empty it. The
conftest check (`_assert_separate_test_database`) calls `pytest.exit` rather than
skipping, because the conftest is the thing that actually truncates — the script
is optional, may never have been run, and is not evidence of anything.

---

## 8. Configuration model

`Settings` is a `pydantic-settings` `BaseSettings`, `extra="ignore"`,
case-insensitive, returned by an `lru_cache`d `get_settings()` singleton. No
module other than this one reads `os.environ` for application settings.

### Derived values

| Property | Rule |
| --- | --- |
| `sqlalchemy_database_uri` | `DATABASE_URL` if set, otherwise assembled from `POSTGRES_*` with the user and password percent-encoded |
| `test_sqlalchemy_database_uri` | `TEST_DATABASE_URL` if set, otherwise the same URL with `_test` appended to the database name |
| `cors_origin_list` | `CORS_ORIGINS` split on commas, empty entries dropped |
| `is_production` / `is_testing` | Derived from `ENVIRONMENT` |

An explicit URL always wins over the parts. That is what lets the same `.env`
drive both a host install and the compose stack, where the URL must name the
`postgres` service rather than `127.0.0.1`.

### Production guards

A `model_validator` refuses to construct settings when `ENVIRONMENT=production`
and either `SECRET_KEY` is still the `dev-insecure-change-me` placeholder or
`DEBUG=true`. Both fail at import time, before uvicorn binds a port. Generate a
real key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

A second normaliser turns an all-whitespace `LOG_FILE` into `None`, so a blank
line in `.env` does not create a file named `" "`.

### Where the variables live

Full tables are in the [README](../README.md#environment-variables); the only one
worth repeating here is the split between the settings object and the entrypoint:
`NEXUS_HOST`, `NEXUS_PORT` and `NEXUS_RELOAD` are read directly by
`backend/run.py`, not by `Settings`, because uvicorn needs them before the
application exists.

---

## 9. Observability

The stdlib `logging` module, not `structlog`. Phase 1 needs exactly one thing
structlog would have provided — per-request context on every record — and
`contextvars` plus a custom `Formatter` does it in a few dozen lines without
adding a dependency to the runtime image (the reasoning is recorded in
`backend/app/core/logging.py`).

| Setting | stdout | file (`LOG_FILE`) |
| --- | --- | --- |
| `LOG_JSON=true` (container default) | one JSON object per line | JSON lines |
| `LOG_JSON=false` | colourised human line, dimmed when stdout is not a TTY or in production | JSON lines |

`configure_logging()` also clears the handlers of `uvicorn`, `uvicorn.access`,
`uvicorn.error` and `sqlalchemy.engine` so they propagate to the root handlers,
and disables `uvicorn.access` outright. It runs in the lifespan with `force=True`
so early import-time records are already formatted.

### Redaction

`redact()` walks mappings and sequences to a depth of 8 (a cyclic payload cannot
stall a logging call) and replaces the value of any key that folds — lowercased,
hyphens to underscores — onto `REDACTED_KEYS`: `password`, `hashed_password`,
`token`, `access_token`, `refresh_token`, `authorization`, `secret`, `secret_key`,
`api_key`, `cookie`, `session`, `csrf_token` and relatives. The JSON formatter
redacts the **key/value pair**, not the value alone, so a scalar
`password="hunter2"` passed through `extra` is still caught.

The request-body preview is the one place `redact()` is *not* enough, because it
can only walk a parsed structure. `_body_preview` therefore renders only a JSON
object or array — re-serialised through `redact()` and capped at
`_MAX_LOGGED_BODY_CHARS` (2000) — and summarises everything else by size:

| Body | Preview |
| --- | --- |
| JSON object or array | The redacted JSON, truncated to 2000 characters with a `...<truncated>` marker |
| Form-encoded, multipart, malformed JSON, a bare JSON scalar | `<not logged: not a JSON object, N bytes>` |
| Body larger than the capture limit | `<not logged: truncated at the capture limit, N bytes>` |
| Empty | `""` |

The point is that a form body carries its credentials in a shape no key-based
scrub can be trusted to recognise — `email=…&password=…` looks like one opaque
string — so quoting it at all would leak it. Recording the size and, separately,
the `content_type` field on the same record tells an operator what arrived
without telling them what was in it.

The cap on *capture* is a different limit from the cap on *preview*. Only when
`LOG_REQUEST_BODY=true` does `BodyCaptureMiddleware` buffer at all, and it keeps
at most `_MAX_CAPTURED_BODY_BYTES` (256 KiB) while still reading and replaying
every byte, so the handler's body is unchanged and one large upload cannot
become unbounded memory in the log path. `body_length` records the true size.

### Access log fields

| Field | Notes |
| --- | --- |
| `method`, `path` | Path is structural and is never redacted. |
| `status_code` | Read back from the outgoing response-start message; 500 when no response started. |
| `duration_ms` | `time.perf_counter`, rounded to 2 places. |
| `client_ip` | Left-most `X-Forwarded-For` entry, else the socket peer. |
| `query` | Redacted. `_redact_query` replaces the *value* of any parameter whose key folds onto `REDACTED_KEYS`, up to the next `&` or `#`, so `?token=abc&page=2` logs as `token=***redacted***&page=2`. `redact()` cannot do this — it walks mappings and sequences, and a raw query string reaches the log untouched without this. |
| `user_agent` | Raw header. |
| `body`, `content_type` | Only when `LOG_REQUEST_BODY=true`; see above. |

Level selection is in `_access_log_level`: ERROR for ≥500, WARNING for ≥400 or
for a duration at or above `SLOW_REQUEST_MS`, INFO otherwise.

---

## 10. Event loop and platform constraints

This is the single most important platform fact in the codebase.

psycopg 3 implements async I/O with `loop.add_reader`. asyncio's default loop on
Windows is `ProactorEventLoop`, which does not provide it, so every database
operation fails with:

```text
InterfaceError: Psycopg cannot use the 'ProactorEventLoop' to run in async mode
```

`SelectorEventLoop` provides the reader callbacks psycopg needs, and is already
the default on Linux and macOS — so selecting it explicitly there changes
nothing, and the platform branch that used to guard it only hid a bug. The whole
fix is now two functions with no branching at all
(`backend/app/core/event_loop.py`):

```python
def selector_loop_factory() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop(selectors.SelectSelector())

def nexus_loop_factory() -> asyncio.AbstractEventLoop:
    return selector_loop_factory()
```

The second half of the rule is *how* the loop is constructed, and it is not a
style preference. The previous version read:

```python
def nexus_loop_factory() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return selector_loop_factory()
    return asyncio.new_event_loop()
```

`asyncio.new_event_loop()` routes through the **active event-loop policy**, and
in the test suite that policy is `_NexusEventLoopPolicy`, whose `new_event_loop`
delegates straight back to `nexus_loop_factory`. So on Linux and macOS the
factory called itself until the stack ran out — an infinite recursion on every
non-Windows run of the suite, the server, and `alembic`. It was invisible here
purely because the suite runs on Windows, where the branch took the
non-recursive path and the bug never executed. It is a real bug that a passing
test run could not have caught, which is why `tests/test_event_loop.py` now
installs a deliberately delegating policy and calls `asyncio.new_event_loop()`
with it, asserting the loop comes back.

Constructing `asyncio.SelectorEventLoop(selectors.SelectSelector())` directly
bypasses the policy entirely, which is what makes the factory safe to hand to a
policy that points back at it.

The factory is applied in exactly three places, and must not be duplicated
anywhere else:

| Consumer | Mechanism |
| --- | --- |
| API server | `backend/run.py` passes `loop="app.core.event_loop:nexus_loop_factory"` to `uvicorn.run` (an import path, because uvicorn resolves `--loop` that way) |
| Migrations | `migrations/env.py` calls `asyncio.run(..., loop_factory=nexus_loop_factory)` |
| Test suite | `tests/conftest.py` installs an `asyncio.DefaultEventLoopPolicy` subclass whose `new_event_loop` delegates to the factory, at import time so it is in place before pytest-asyncio snapshots the policy |

The third consumer is what makes the direct construction mandatory rather than
merely tidy: two of the three call sites are safe either way, but the policy
consumer is a fixed point, and only a factory that never asks the policy a
question can sit inside one.

**Consequence:** `cd backend && python run.py` is the supported way to start the
API on every OS. A bare `uvicorn app.main:app` starts on Windows and then fails on
every query; `scripts/dev.sh` uses `python run.py` for exactly this reason and
says so in a comment.

---

## 11. Process model

The backend runs **one** worker per container. That is not a default to be tuned —
it follows from two per-process pieces of state:

| State | Consequence of a second worker |
| --- | --- |
| `RevocationStore` singleton | A logout recorded in worker A is invisible to worker B, so a revoked refresh token stays replayable. |
| Lazy engine + session factory | One pool per worker; `NEXUS_RELOAD=true` forks a second process that would carry its own pool and its own empty denylist. |

`run.py` therefore defaults `NEXUS_RELOAD` to `false` and Compose forces it to
`"false"`. Horizontal scaling, when it is ever needed, comes from running more
containers behind a load balancer — not from more workers in one container. The
backend Dockerfile notes the same constraint next to its `CMD`.

---

## 12. Frontend architecture

### Provider stack

`main.tsx` renders `StrictMode → AppProviders → RouterProvider`, and
`app/providers.tsx` nests:

```text
QueryClientProvider      server state, 30 s staleTime, no refetch on focus
└── ThemeProvider        resolves light|dark|system, applies `.dark` to <html>
    └── TooltipProvider  Radix, 200 ms delay
        └── AuthBootstrap  calls store.hydrate() once, then renders
```

The theme is applied in `useLayoutEffect`, and a blocking inline script in
`index.html` applies the persisted theme *before* the bundle executes. The script
can only read a plain `light` / `dark` string, so the store mirrors the resolved
theme into `nexus-theme` while the raw preference (`light` | `dark` | `system`)
lives in the persisted `nexus.theme` key. Without the mirror, a stored light theme
would paint dark for one frame on every reload.

### Routing

`createBrowserRouter` in `routes/router.tsx`; every page is a `lazy()` import
declared in `routes/lazy-pages.ts` so the router file stays a pure route table.

| Route group | Guard | Elements |
| --- | --- | --- |
| `/` | — | Redirects to `/dashboard` |
| `AppLayout` children | `RequireAuth` | dashboard, projects, tasks, planner, knowledge, search, analytics, developer, learning, career, assistant, experiments, settings, `*` → not found |
| `RequireAnonymous` children | `RequireAnonymous` | `/login`, `/register` |

While `status === 'initializing'` (a persisted session is being verified against
`GET /auth/me`) both guards render a branded `BootScreen` rather than redirecting,
which is what stops a signed-in user from seeing the login page on every reload.
`RequireAuth` remembers the intended path in `location.state.from`;
`RequireAnonymous` sends an authenticated user to `/dashboard`.

### The module registry

`frontend/src/features/modules/catalog.ts` declares every destination once:
`to`, `label`, header `summary`, long-form `vision`, `phase`, icon, capabilities,
metrics and search keywords. The sidebar, the command palette, the page headers
and the placeholder bodies all render from it, and `getModule()` throws on an
unknown path, so a route that drifts from the registry fails loudly. Adding a
module is a single-file change plus a route entry.

`ALL_NAV_ITEMS` — twelve modules plus Settings — is the palette's destination
list: 13 entries, filtered with a 120 ms debounce and driven by ArrowUp/ArrowDown,
Enter and Escape.

### Data access

| Layer | File | Rule |
| --- | --- | --- |
| Transport | `lib/api-client.ts` | Framework-agnostic typed `fetch` wrapper: base URL, bearer injection, timeout (`DEFAULT_TIMEOUT_MS` = 30 000), `AbortSignal` support, and a single `ApiError` type. No React, no React Query. |
| Endpoints | `services/*.ts` | One exported function per endpoint. `auth.ts` and `health.ts` are the only two endpoint files today; `errors.ts` holds the single `unknown → ApiError` conversion every caller shares. |
| Server state | `app/query-client.ts` | Retries suppressed for 4xx (a rejected request does not become accepted by asking again); transport failures get two attempts, which covers "the backend is still starting". |
| Client state | `stores/*.ts` | Zustand: `auth-store` (persisted to `nexus.auth`, `status` deliberately not persisted) and `theme-store`. |

The auth store wires two hooks onto the shared client at module load:

```ts
apiClient.setTokenGetter(() => useAuthStore.getState().accessToken)
apiClient.setUnauthorizedHandler(() => useAuthStore.getState().renewAccessToken())
```

The second one is the 401-recovery interceptor: an expired access token no
longer strands a signed-in user on whatever page they were on. `ApiClient`
catches a 401, asks the handler for a fresh token, and replays the original
request once.

That only works if concurrent callers do not each try to rotate. Refresh tokens
are **single-use server-side** (see [Auth flows](#6-authentication-and-authorisation)),
so a burst of parallel 401s firing four refreshes would have three of them
present an already-spent token, get a 401 back, and tear down the pair the
winner had just stored. So the refresh path is guarded at three points:

| Guard | Where | Covers |
| --- | --- | --- |
| `recovery` (a shared promise) | `ApiClient`, `recoverToken()` | A burst of 401s from different requests shares one renewal. |
| `refreshInFlight` (a shared promise) | `auth-store`, `refreshSession()` | `hydrate()` and any 401 recovery share one rotation of the one refresh token. |
| `hydrateInFlight` (a shared promise) | `auth-store`, `hydrate()` | A StrictMode double-mount cannot start two verifications. |

The three live at module scope, not in store state, so they outlive any single
store read.

`hydrate()` verifies a persisted session against `/auth/me`; on 401 it attempts
one refresh and retries. A refresh failure is then split by outcome, because a
backend that did not answer says nothing about the validity of the token:
`unreachable` (transport error, timeout, or 5xx) keeps the stored pair and
reports a retryable state, and only an actual rejection (`unusable`) clears the
session. Signing the user out over a network blip would cost them a re-login for
nothing. Register logs the new account in immediately rather than showing a
second form. Logout clears local state in a `finally` — a failed server call must
never trap the user in a signed-in shell.

A sign-out, a rejected session and a fresh sign-in all announce themselves
through `onSessionChange()`, which the query layer subscribes to so cached data
cannot outlive the session that fetched it. The store does not import the query
client to do it.

### Shell and design system

`components/layout/app-shell.tsx` owns the responsive frame: a fixed sidebar above
`lg` that can collapse to icons (persisted under `nexus.sidebar.collapsed`), and an
off-canvas sheet with an overlay button below it. The sidebar width is published as
a CSS variable so the content gutter animates with it; the sheet state is derived
from the breakpoint, so resizing to desktop cannot strand an open sheet.

Design tokens are HSL channel triplets in `src/index.css`, mapped to Tailwind
utilities in `tailwind.config.ts` with `<alpha-value>` placeholders.
`components/ui/` holds shadcn-style primitives over Radix with `cva` variants.

### Build

`manualChunks` in `vite.config.ts` splits `node_modules` by first match, narrow
rules before broad ones: `charts`, `icons`, `radix`, `router`, `data`, `react`.
Routes are additionally split by the `lazy()` calls above.

Exact byte sizes of the current `frontend/dist/assets/*.js`, uncompressed, as
built by `npm run build`:

| Chunk | Bytes |
| --- | --- |
| `react` (largest vendor) | 222 295 |
| `radix` | 113 444 |
| `router` | 91 288 |
| `index` (entry) | 84 474 |
| `data` | 35 764 |
| `icons` | 13 240 |
| `dashboard-page` (largest route) | 11 903 |
| `settings-page` | 4 828 |
| per-page placeholders | ~0.36 kB each |

Ten of the eleven placeholder routes render the shared `ModulePage` and cost
358–370 bytes each; the eleventh, `/search`, adds a small card for its shortcut
hint and lands at 1 239. Route splitting is therefore paying for the pages that
will grow, not the ones that exist — only `dashboard-page` (11 903) and
`settings-page` (4 828) are substantive today.

The dev server and `vite preview` (4173) share the same proxy configuration, so a
previewed production bundle behaves like the reverse proxy that will eventually
front the API.

---

## 13. Testing architecture

### Backend

```bash
# from backend/
python -m pytest                        # 188 collected, needs nexus_test
python -m pytest -m "not integration"   # 145 pass, 43 deselected, no database required
```

`pytest.ini` sets `testpaths = tests`, `pythonpath = .`, `asyncio_mode = auto`,
function-scoped event loops (`asyncio_default_fixture_loop_scope` and
`asyncio_default_test_loop_scope`), `--strict-markers --strict-config`, and
registers the `integration` marker.

**A `DeprecationWarning` from `app.*` is an error**, and the filter order is what
makes that true:

```ini
filterwarnings =
    ignore::DeprecationWarning
    error::DeprecationWarning:app.*
```

pytest *prepends* each ini entry to `warnings.filters`, and the **last** match
wins — so a blanket `ignore` listed second would swallow the escalation and make
the rule dead. The blanket ignore therefore has to come first.
`tests/test_warnings.py` asserts all three facts: that a warning attributed to an
`app.*` module raises, that one from elsewhere stays ignored, and that the
app-scoped rule really does precede the blanket ignore in `warnings.filters`.
That last assertion is the one that catches someone reordering the file into a
rule that looks right and does nothing.

| File | Tests (non-integration) | Covers |
| --- | --- | --- |
| `test_middleware.py` | 40 | Access-log fields, body-preview redaction, query redaction, capture cap vs. verbatim replay, non-default `create_app(settings=…)` wiring (CORS, OpenAPI/docs/redoc URLs) |
| `test_logging.py` | 41 | JSON and human formatter shape, `REDACTED_KEYS` folding, hyphenated keys, `ContextFilter` request-id propagation |
| `test_security.py` | 23 | JWT issue/verify, `type` enforcement, tamper and expiry rejection, bcrypt behaviour |
| `test_error_handling.py` | 16 | The 5xx path: what a client may see, `X-Request-ID` on a 500, the no-echo rule for a 5xx `detail`, unmapped statuses as 4xx |
| `test_config.py` | 14 | Settings assembly, derived URLs, production guards, the `get_settings` cache |
| `test_health.py` | 5 (+3 integration) | Liveness never touching the database, header propagation, and the **degraded** path; the detailed endpoint and the lifespan are integration |
| `test_event_loop.py` | 3 | The factory survives a delegating policy, builds a `SelectorEventLoop` with `add_reader`, and yields a fresh loop each call |
| `test_warnings.py` | 3 | The `filterwarnings` rules above |
| `test_errors.py` | integration | The envelope, the code table, `FORBIDDEN_FRAGMENTS` — the shared leak assertions other files import |
| `test_auth.py` | integration | Register / login / refresh / logout / me end to end |
| `test_repositories.py` | integration | SQL against the real test database |
| `test_migrations.py` | integration | Linear single-head chain, schema present, no drift |

Two of these deserve a note on what they changed.

`test_health.py`'s **degraded** path is real coverage, not inspection: it patches
`check_database_connection` where the router imported it — `app.api.v1.health`,
not `app.db.session`, or the real probe stays in place and reaches for the
database the test exists to avoid — and asserts the endpoint answers **200** with
`status: "degraded"` and `database.status: "unavailable"`. Liveness is asserted
the same way, by making any database access an `AssertionError`.

`test_error_handling.py` exists because the default `httpx.ASGITransport` sets
`raise_app_exceptions=True`: an exception escaping the app is re-raised inside the
test, so the rendered 500 is never visible and the application's own catch-all
handler is untestable. Both it and the shared `non_raising_client` fixture turn
that off (`ASGITransport(app=app, raise_app_exceptions=False)`) for exactly the
tests that need to observe a failure.

Fixtures in `conftest.py`:

| Fixture / helper | What it does |
| --- | --- |
| `settings` | Clears the `get_settings` cache before and after each test, so a monkeypatched variable cannot leak. |
| `make_settings` | Builds a `Settings` from explicit environment overrides, clearing the cache on the way out. |
| `test_database_url` (session) | Refuses to start if `TEST_DATABASE_URL` resolves to the application database, creates `nexus_test` if missing, enables `pg_trgm` / `unaccent`, and migrates it to `head`. |
| `engine` (session) | `NullPool` engine on the test database, installed as the application's engine, restored on teardown. |
| `truncated_database` | `TRUNCATE … RESTART IDENTITY CASCADE` over every managed table before each test. |
| `db_session` | A session on the test database; `rollback()` on exit discards only what a test left uncommitted. |
| `offline_client` | A client with no database fixture in scope, for DB-free assertions. |
| `non_raising_client` | A client that observes the rendered 5xx instead of re-raising it (see above). |
| `client` | In-process `AsyncClient` over `app` with the database fixtures in scope. |
| `assert_error_envelope` | Asserts the status, the exact outer and inner key sets, the code, a non-empty message, and `error.request_id == response.headers["X-Request-ID"]`. |
| `_preserved_logging` | Saves and restores the stdlib logging tree around Alembic's `fileConfig`. |

Two rules the database fixtures exist to enforce:

- **The schema comes from the migration, never from the models.**
  `Base.metadata.create_all` is deliberately never used: a schema built from the
  models would prove nothing about the migration.
- **`TEST_DATABASE_URL` must not name the application database.**
  `_assert_separate_test_database` calls `pytest.exit`, not `pytest.skip`,
  because `truncated_database` really does `TRUNCATE` — a skip here would be a
  guard that silently stops guarding. `scripts/create_test_database.py` refuses
  the same collision, but it is optional and may never have been run.

The test engine uses `NullPool` and is installed as the application's engine for
the whole session. This is required, not an optimisation: `pytest.ini` scopes the
asyncio loop to a single test, so a pooled connection opened under one loop would
be reused under the next.

`ASGITransport` does not run the lifespan, so no test may assume the startup or
shutdown hooks have fired — the `engine` fixture stands in for what they would
have done. `test_health.py::test_app_lifespan_runs_without_error` is the one
test that drives `lifespan_context` explicitly.

### Frontend

```bash
# from frontend/
npm test               # vitest run — 30 tests in 10 files
npm run typecheck      # tsc -b
npm run lint           # eslint .
npm run build          # tsc -b && vite build
```

Vitest runs in `jsdom` with `src/test/setup.ts`. The ten test files are
`lib/api-client`, `lib/utils`, `stores/theme-store`, `features/modules/catalog`,
`components/ui/button`, `components/ui/card`, `components/feedback/error-state`,
`components/feedback/app-error-boundary`, `components/layout/app-shell` and
`pages/app-shell.smoke` — which exercises the real routes: sign-in through the
actual auth endpoints, Ctrl+K palette navigation, and the retryable error state
when the backend is down.

### What is not covered

Single-user local-first product: no load, concurrency, migration-from-an-older-
schema, or browser-matrix testing.

### What has not been run

Every number in the two command blocks above was produced on Windows. The 43
`integration` tests have **never been executed** — there is no PostgreSQL and no
Docker in the environment this document was last revised in, so `nexus_test` is
never created, and the repository, auth, migration and detailed-health assertions
are unverified. They are written against the schema and the endpoints described
here, but "the suite collects 188 tests" is not "the suite passes 188 tests".

Likewise `docker-compose.yml` has never been executed by `docker compose` — it
is structurally validated by `scripts/verify_compose.py`, which confirms that all
14 interpolated variables are documented in `.env.example`, and which cannot tell
you that the stack actually starts.

And the Linux/macOS behaviour of the event-loop factory is asserted by a unit
test that reproduces the recursion, not by having run the suite on either
platform. The bug it covers was real precisely *because* it could not be caught
here.

---

## 14. Container topology

`docker-compose.yml`, project `name: nexus`, three services, every value
interpolated as `${VAR:-default}` from `.env` — nothing is hardcoded.

| Service | Image / build | Depends on | Start command |
| --- | --- | --- | --- |
| `postgres` | `postgres:16-alpine` | — | entrypoint, with `pg_isready` healthcheck and the init script mounted read-only |
| `backend` | build `./backend` | `postgres` **healthy** | `alembic upgrade head && exec python run.py` |
| `frontend` | build `./frontend` | `backend` **healthy** | `npm run dev` (Vite dev server, not a static bundle) |

Three decisions are worth reading in the file itself:

- **The backend migrates on start.** The image ships `migrations/` and Alembic but
  runs neither at build time nor as an entrypoint, so a fresh volume would serve
  requests against an empty schema. `upgrade head` is a no-op once current.
- **The frontend image runs the dev server**, not a built bundle, and sets
  `VITE_API_BASE_URL=/api/v1` so the browser is same-origin and the Vite proxy
  forwards to `VITE_DEV_PROXY_TARGET=http://backend:8000`.
- **Healthchecks gate startup**, so `depends_on: condition: service_healthy`
  orders postgres → backend → frontend instead of racing them.

The backend Dockerfile is multi-stage: an `awk` extraction at the `DEV MARKER`
line strips pytest and ruff from the runtime image, and the runtime stage runs as
uid 10001 with `libpq5` and `curl` only.

---

## 15. Extension roadmap

<a id="extension-roadmap"></a>

The seams below exist in Phase 1. None of the *destinations* is implemented; the
"Today" column says what is already in place to reach it.

| Seam | Today | Later | Touch points that must not change |
| --- | --- | --- | --- |
| Token revocation | `RevocationStore`, in-process | Redis-backed denylist | `RevocationStore` interface, `get_revocation_store()` |
| Horizontal scaling | one worker per container | more containers behind a load balancer | once revocation is shared, `run.py`'s single-worker assumption relaxes |
| Background work | none | worker process | `scripts/` for process orchestration; jobs need the same event-loop factory |
| Search | `pg_trgm` + `unaccent` enabled | retrieval index over Knowledge | extension availability is already a prerequisite |
| Local LLM | none | Ollama-backed assistant | never leaves the machine; the catalog already fixes the Phase 9 contract |
| Repository analysis | none | local git history scan | read-only, from disk |
| Module data | `catalog.ts` registry | per-module routers and pages | add a route entry and a catalog entry; nothing else |
| Profile update | `UserService` + `get_user_service`, no router | a `users` router and a test | the service translates `IntegrityError` itself, so the router needs no try/except |

Phase numbering is not invented here — it is the `phase` field in
`frontend/src/features/modules/catalog.ts`, and the module list it drives is the
single source of truth for the sidebar, the palette and the roadmap.

---

## 16. Design decisions

| Decision | Rationale | Cost accepted |
| --- | --- | --- |
| Start the API with `run.py`, not bare `uvicorn` | The event loop must be chosen before the app exists; one factory, three consumers | An extra entrypoint file; a bare `uvicorn` command silently misbehaves on Windows |
| `127.0.0.1` in URLs, never `localhost` | `localhost` → `::1` on Windows and psycopg is IPv4 only; the failure is a hang, not an error | Slightly less portable-looking configuration |
| Empty `sqlalchemy.url` in `alembic.ini` | Credentials must never live in a tracked file | One extra indirection through `get_settings()` |
| Migrate on container start | A fresh volume cannot serve traffic against an empty schema | Startup pays for `upgrade head`; the healthcheck `start_period` covers it |
| Empty `sqlalchemy.url`, explicit DDL, no model import in revisions | Editing a model must not rewrite history | Drift is possible by hand — caught by `alembic check` and a test |
| Service layer never imports FastAPI | Keeps business rules testable and the dependency graph acyclic | Errors travel as exceptions instead of return values |
| Repository never raises domain errors | One translation point; `IntegrityError` becomes `409` in one place | Services must be prepared for raw DB exceptions |
| `bearer_scheme(auto_error=False)` | FastAPI's built-in failure is a 403 with the wrong shape; ours is the shared 401 envelope | Every auth dependency must handle the `None` credentials case |
| `type` claim + expected-type check | Stops a refresh token being used as a bearer credential | Two token types to reason about |
| In-memory `RevocationStore` | Real logout in Phase 1 with no new dependency | Per-process only; forces single-worker, and caps future scale-out |
| `expire_on_commit=False` | Response models read attributes without a reload | Callers must not assume a refresh happened |
| `pool_pre_ping=True` | A database restart should not surface as a request error | One extra round trip per checkout |
| Stdlib logging, not structlog | One thing needed (request context), achieved with `contextvars` + a `Formatter` | Handlers, formatters and a filter to maintain by hand |
| Install `RequestContextMiddleware` **above** `ServerErrorMiddleware` | A middleware registered with `add_middleware` sits inside the layer that renders a 500, so it never observes that response and cannot stamp `X-Request-ID` on it — every 500 would carry a `request_id` in its body and none in its header | Overriding `build_middleware_stack` is a private-ish Starlette hook, and the middleware must stay pure ASGI and stamp the header on `http.response.start` itself |
| Header and error body stamped from one id, on every status | They come from the same binding in the same middleware call, so `error.request_id == X-Request-ID` cannot drift — including on the 500 path | None; this is a consequence, not a compromise |
| Decoy bcrypt verify on an unknown e-mail | A shared error message hides the account from the response *text*; only a comparable runtime hides it from the response *clock* | An unknown address pays the full bcrypt cost (~250 ms) too — a deliberate DoS surface on a single-user local app |
| Purge the revocation denylist on `revoke()` as well as on lookup | A logout-only workload never calls `is_revoked`, so read-time purging alone makes the bound depend on authenticated traffic continuing | A full dict scan inside the write lock; irrelevant at Phase 1 volume |
| A 5xx never echoes its `detail` | It is text the application did not author for a client, and it can carry SQL, paths or credentials | A legitimate 5xx `detail` set by a developer is silently discarded client-side; it is logged instead |
| `internal_error` reserved for 5xx; unmapped 4xx → `bad_request` | The frontend branches on `code`; reporting an oversized upload or a wrong content type as a server fault sends the user down the wrong recovery path | Several 4xx statuses share one code, so `code` alone cannot distinguish a 413 from a 402 |
| Build the event loop directly, never via `asyncio.new_event_loop()` | That call routes through the active policy, and the test suite's policy points back at this factory — the two are a fixed point | One loop type everywhere, including platforms where it was already the default |
| One event-loop factory, three consumers, never duplicated | A second copy is a second thing to forget when the psycopg constraint changes | None; the cost is remembering the three call sites |
| `DB_PROBE_TIMEOUT_SECONDS` alongside `DB_POOL_TIMEOUT` | `pool_timeout` bounds waiting for a *pooled* connection; it says nothing about the TCP/TLS handshake behind one, which is exactly how a filtered port fails | Two tunables that look like the same knob and are not |
| Dispose the engine only if one was built | `get_engine()` is lazy; calling it in the shutdown hook would build an engine from the default settings purely to close it | Reaching into `db_session._engine` rather than through the accessor |
| `lifespan` reads `app.state.settings` | `create_app(settings=…)` already builds CORS, docs URLs and the API prefix from those settings; logging and the database probe used the global singleton and disagreed | One extra attribute, and `app.state` becomes load-bearing |
| Contextvar first, `request.state` fallback | Now that the middleware sits outside `ServerErrorMiddleware`, the catch-all runs with the contextvar still bound — so the contextvar is the *primary* source and `request.state` is a safety net for a path that no longer exists | Two sources of truth for one id |
| `non_raising_client` for tests that assert on a rendered 5xx | The default transport re-raises, which makes the application's own catch-all handler unobservable | One more client fixture, and a rule about which tests use which |
| `pytest.exit` when `TEST_DATABASE_URL` names the application database | `truncated_database` really does truncate; a skip would be a guard that silently stops guarding | A configuration mistake stops the whole session rather than skipping the affected tests |
| Frontend 401 recovery + single-flighted refresh | An expired access token should not strand the user, but refresh tokens are single-use server-side — concurrent refreshes would destroy the winner's fresh pair | Three module-level in-flight promises to reason about, and a replay path that must not loop |
| Same-origin `/api/v1` under Compose | Keeps CORS and cookies out of the picture in dev; the proxy is an accurate stand-in for a reverse proxy | Two `VITE_API_BASE_URL` values depending on mode |
| Module registry as the single declaration | Sidebar, palette, headers and placeholders cannot drift apart | Adding a module still needs a route entry |
| React Query for server state, Zustand for client state | Caching/polling and session/theme are different problems | Two state libraries |
| Placeholder pages that render an em dash | No fabricated data; the UI states plainly what does not exist | Screenshots and demos look emptier than a mock would |

---

## See also

| Document | Contents |
| --- | --- |
| [`../README.md`](../README.md) | Setup, quick start, environment variables, commands, troubleshooting, roadmap |
| [`api-conventions.md`](api-conventions.md) | Endpoint contract: versioning, error codes, request ids, pagination, the endpoint checklist |
| [`development.md`](development.md) | Clean-machine setup, daily workflow, adding an endpoint or a page, testing and style conventions |