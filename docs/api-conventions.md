# API conventions

The contract every NEXUS endpoint follows, and the rules a new endpoint must obey.
`README.md` covers how to *start* the stack; this document covers what the API
looks like once it is running.

**Status.** Phase 1 (technical foundation). Eight paths exist, described in
[Endpoint catalogue](#endpoint-catalogue). Everything about the modules
(Projects, Planner, Knowledge, Search, Analytics, …) is a frontend placeholder —
there is no module API yet, and the conventions below are the shape it will take.

---

## Contents

- [Base URL and versioning](#base-url-and-versioning)
- [Endpoint catalogue](#endpoint-catalogue)
- [Request conventions](#request-conventions)
- [Response conventions](#response-conventions)
- [The error envelope](#the-error-envelope)
- [Error codes](#error-codes)
- [Validation errors](#validation-errors)
- [Request correlation (`X-Request-ID`)](#request-correlation-x-request-id)
- [Authentication](#authentication)
- [CORS and response headers](#cors-and-response-headers)
- [Pagination](#pagination)
- [Health semantics](#health-semantics)
- [The client contract](#the-client-contract)
- [Checklist for a new endpoint](#checklist-for-a-new-endpoint)
- [Worked example — the shape a future endpoint takes](#worked-example--the-shape-a-future-endpoint-takes)
- [Testing an endpoint](#testing-an-endpoint)
- [Known gaps](#known-gaps)

---

## Base URL and versioning

| Setting | Value | Where |
| --- | --- | --- |
| Version prefix | `/api/v1` | `settings.api_v1_prefix` in `backend/app/core/config.py` |
| Backend origin (local) | `http://127.0.0.1:8000` | `NEXUS_HOST` / `NEXUS_PORT`, read by `backend/run.py` |
| Browser-facing base | `VITE_API_BASE_URL` | `.env`; the code default is the relative `/api/v1` |

The prefix is mounted once, in `backend/app/main.py`:

```python
application.include_router(api_router, prefix=settings.api_v1_prefix)
```

Three paths deliberately sit **outside** the prefix: `GET /health` (liveness),
`GET /` (service metadata) and the documentation mounts. Liveness in particular
must not move under a version prefix — an orchestrator restart loop should not
have to be re-pointed when the API is versioned.

Rules:

- A breaking change to a response shape, a field meaning or an error code gets a
  **new prefix** (`/api/v2`), not a silent change to `/api/v1`.
- Adding an endpoint, an optional request field, an optional response field or a
  new error `code` is not breaking. Existing fields are never removed, renamed or
  retyped inside a version.
- New modules land under `/api/v1/<module>/…` — `/api/v1/projects`, then
  `/api/v1/tasks`. There is no `/api/v1/v1`.
- Version the *contract*, not the deployment. `APP_VERSION` in the health payload
  is the build version and moves with every release; it is not the API version.

Interactive documentation, when the server is running:

| Surface | URL |
| --- | --- |
| Swagger UI | `http://127.0.0.1:8000/docs` |
| ReDoc | `http://127.0.0.1:8000/redoc` |
| OpenAPI document | `http://127.0.0.1:8000/openapi.json` |

Start the server with `python run.py` **from `backend/`**. A bare
`uvicorn app.main:app` starts on Windows but cannot reach the database — see the
README's troubleshooting section.

---

## Endpoint catalogue

Everything the API serves today. Nothing else responds.

| Method | Path | Auth | Success | Notes |
| --- | --- | --- | --- | --- |
| `GET` | `/` | no | 200 | Service metadata and an endpoint index (`service`, `version`, `environment`, `status`, `api_version`, `api_prefix`, `links`) |
| `GET` | `/health` | no | 200 `{"status":"ok"}` | Liveness. Never touches the database |
| `GET` | `/api/v1/health` | no | 200, `status: "healthy"\|"degraded"` | Metadata plus a timed `SELECT 1` probe |
| `POST` | `/api/v1/auth/register` | no | 201, `UserRead` | 409 `conflict` if the email is taken |
| `POST` | `/api/v1/auth/login` | no | 200, `TokenPair` | 401 `unauthorized` for any credential failure |
| `POST` | `/api/v1/auth/refresh` | no | 200, `TokenPair` | Single-use rotation: the presented token is revoked |
| `POST` | `/api/v1/auth/logout` | optional | 204, empty body | Revokes each token it is given. **Send the refresh token in the body** — the bearer header alone leaves it valid. See [Authentication](#authentication) |
| `GET` | `/api/v1/auth/me` | bearer | 200, `UserRead` | 401 `unauthorized` for missing/invalid/expired/revoked tokens |

Declared schema facts, taken from the generated OpenAPI document:

- The security scheme is `HTTPBearer` (`type: http`, `scheme: bearer`,
  description "JWT access token"), so Swagger UI offers an **Authorize** button.
  Only `GET /auth/me` and `POST /auth/logout` carry
  `security: [{"HTTPBearer": []}]`; logout's parameter is optional.
- **Error responses are not declared in the OpenAPI schema.** An operation lists
  only its 2xx (plus 422 where Pydantic validation applies). A 401/404/409 raised
  at runtime is documented here and in the Swagger description banner, not in the
  per-operation `responses`. See [Known gaps](#known-gaps).

---

## Request conventions

- **Bodies are JSON.** `Content-Type: application/json`. Pydantic v2 parses and
  validates; a malformed body produces a 422 envelope, not a 500.
- **Field names are `snake_case`** on the wire in both directions — the Python
  models and the TypeScript types in `frontend/src/types/api.ts` use the same
  spelling. No aliases, no camelCase bridge.
- **Emails are normalised before validation**: trimmed and lower-cased by a
  `mode="before"` validator, and stored the same way. `  ADA@Nexus.DEV  ` and
  `ada@nexus.dev` are the same account, and uniqueness is checked on the
  normalised value.
- **Constraints are declared in the schema, not the service.** `email` is capped
  at 320 characters and matched against a deliberate shape regex (not RFC 5322 —
  `EmailStr` would need a dependency the backend does not ship); `full_name` at
  255; a new password at 8–128. bcrypt only ever hashes the first 72 bytes of
  input, so `security._bcrypt_bytes` truncates before hashing; be aware of that
  ceiling when choosing a maximum password length.
- **Secrets are write-only.** `hashed_password` is absent from every model that
  can leave the process, and no response body may echo the submitted password
  (`backend/tests/test_auth.py` asserts this).
- **Query parameters** are scalars (`string`, `int`, `bool`, `null`). Nested
  structures go in the body. Unknown query parameters are ignored, not rejected.
- **Send `Accept: application/json`** (the client does this by default).
- **No cookie auth.** `Authorization: Bearer …` is the only credential; there is
  no session cookie and no CSRF surface.

---

## Response conventions

- **2xx bodies are the resource itself** — no `{"data": …}` wrapper on success.
  The wrapper exists only for errors, which keeps the success path trivially
  typed on the client.
- **201 for creation**, with the created resource as the body and no `Location`
  header requirement. **204 with a zero-length body** for a delete or a
  successful action that has nothing to return; the client treats an empty 204
  as `undefined`, never as an empty object.
- **Identifiers are UUID v4 strings**, generated application-side (see
  `UUIDPrimaryKeyMixin` in `backend/app/db/base.py`) so an id is known before the
  row is flushed and no row count is leaked.
- **Timestamps are ISO-8601 UTC.** Pydantic serialises `DateTime(timezone=True)`
  columns as ISO-8601; the hand-built health `timestamp` is
  `2026-01-01T00:00:00.000Z` (millisecond precision, `Z` suffix). The
  TypeScript alias is `ISODateTimeString = string` — the shape is a convention,
  not a compile-time guarantee.
- **A field that has no value is `null`, never absent and never `""`.** Absent is
  reserved for "not applicable to this representation".
- **One model per representation.** `UserRead` is the only user shape that leaves
  the process; the ORM model never is.

---

## The error envelope

Every non-2xx response has exactly this body, with exactly these four keys:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request body or query parameters failed validation.",
    "details": {
      "errors": [
        {
          "field": "password",
          "message": "String should have at least 8 characters",
          "type": "string_too_short",
          "context": { "min_length": "8" }
        }
      ]
    },
    "request_id": "1f0a1d0c-6f2a-4a1e-9b0f-6f4c1d2e3a4b"
  }
}
```

| Field | Type | Contract |
| --- | --- | --- |
| `code` | string | Stable `snake_case` identifier. **Branch on this.** Never on `message` |
| `message` | string | Always present, always non-empty, always safe to render to a user. No stack trace, no SQL, no driver or module name, no credentials |
| `details` | object \| null | Machine-only structured context. `null` when there is nothing to add. Never rendered verbatim |
| `request_id` | string | The same value as the `X-Request-ID` response header. **Present on every response, including a 5xx** — the two are stamped from one source and never disagree |

Rules that are enforced by tests in `backend/tests/test_errors.py`:

- The top-level payload has exactly one key, `error`.
- `code` is lowercase `snake_case` and alphanumeric.
- A response body may not contain `traceback`, `psycopg`, `sqlalchemy`,
  `asyncpg`, `alembic`, a file path fragment, a SQL keyword, a column reference
  or an internal package path.
- An unhandled exception is logged server-side with its traceback and answered
  with `internal_error` / "An internal server error occurred." — the client learns
  nothing but the `request_id` needed to find the log line.

### 5xx is deliberately opaque

For **any** status `>= 500` — the catch-all handler *and* an
`HTTPException`/`StarletteHTTPException` that carries a 5xx — the envelope is
fixed:

| Field | 5xx value |
| --- | --- |
| `code` | `internal_error` (a 5xx always maps there; see [Error codes](#error-codes)) |
| `message` | The single constant `"An internal server error occurred."` (`_INTERNAL_ERROR_MESSAGE` in `backend/app/core/exceptions.py`) |
| `details` | Always `null` |
| `request_id` | The correlation id — the only thing that survives |

The originating exception's own text is logged (`unhandled_exception` carries the
exception type; `http_exception` carries `detail`) and is reachable by
`request_id` alone. It is never echoed, because that text is text NEXUS did not
write — a framework or dependency string, or application text about a failure —
and it can carry SQL, module paths or credentials.

**A handler must not rely on a 5xx `detail` reaching the client.** If you raise
`HTTPException(500, "cannot reach ledger shard 3")` from a service, the user sees
`"An internal server error occurred."` and a developer sees your sentence only in
the log. Passing `detail` to a 5xx is therefore a private logging channel at
best; do not build user-facing behaviour on it. Raising a domain error with a
5xx is not a way around this either — the same constant is used for every 5xx.

Raise the errors from the **service** layer, never from a router and never by
returning an error-shaped `JSONResponse` by hand:

```python
from app.core.exceptions import ConflictError, NotFoundError, UnauthorizedError

if user is None:
    raise NotFoundError("User not found.")
```

`install_exception_handlers` in `backend/app/core/exceptions.py` turns
`NexusError`, `RequestValidationError`, `StarletteHTTPException` and bare
`Exception` into the envelope, so a new error type only needs a `code`, a
`status_code` and a `default_message`.

---

## Error codes

Declared in `ErrorCode` (`backend/app/core/exceptions.py`) and mapped to HTTP
status by `_STATUS_CODE_TO_ERROR_CODE`. That table covers eight statuses; the
rest resolve through `_status_code_to_code`:

| `code` | Status | Raised by | Typical cause |
| --- | --- | --- | --- |
| `validation_error` | 422 | `RequestValidationError` handler; the `ValidationError` domain class | Body/query failed schema validation |
| `not_found` | 404 | `NotFoundError`, and any unmatched path | Unknown route, or a resource id that does not exist |
| `unauthorized` | 401 | `UnauthorizedError` | Missing, malformed, expired, wrong-type or revoked token; wrong credentials; inactive account |
| `forbidden` | 403 | `ForbiddenError` | Authenticated but not permitted (superuser check) |
| `conflict` | 409 | `ConflictError` | Uniqueness violation, or a collision with current state |
| `bad_request` | 400 | Fallback for any unmapped 4xx; reserved as a domain code | Malformed request that is not schema-invalid |
| `method_not_allowed` | 405 | `StarletteHTTPException` | Wrong verb on a known path |
| `rate_limited` | 429 | Reserved — no rate limiting is implemented | — |
| `internal_error` | 500 | `NexusError` default, `StarletteHTTPException` 5xx, the catch-all handler | Unexpected failure; the client is told nothing |

The two rules that are easy to get backwards:

- **An unmapped 4xx becomes `bad_request`, never `internal_error`.** A status
  outside the table — 413 (payload too large), 415 (unsupported media type),
  402, 418, anything — resolves to `bad_request`. Reporting a caller's mistake
  as `internal_error` would blame the server for a failure the server did not
  cause, and would send the caller looking in the wrong logs.
- **`internal_error` is reserved for 5xx.** `_status_code_to_code` only returns
  it when `status_code >= 500`. No 4xx can ever carry it.

So if an endpoint needs an exact code/status pair, raise a domain error with the
`code` and `status_code` it sets on the class — do not reach for `HTTPException`,
whose code is derived from the status and may be the fallback.

Also attached to the response:

- A **401 from a domain error** carries `WWW-Authenticate: Bearer`.
- A 405 carries Starlette's `Allow` header.
- Framework-generated messages (for example the 404 body is
  `{"code":"not_found","message":"Not Found",…}`) are not curated. They are still
  user-safe; do not treat them as stable copy. The 5xx message *is* fixed, per
  [5xx is deliberately opaque](#5xx-is-deliberately-opaque).

### Client-only codes

The frontend client manufactures a few codes that never come from the server. They
exist so UI code has one error shape to branch on, and they always carry
`status: 0` (no HTTP response was received):

| `code` | `status` | Meaning |
| --- | --- | --- |
| `timeout` | 0 | The client aborted the request after its 30 s default |
| `network_error` | 0 | `fetch` rejected — the server is not listening, or DNS/TLS failed |
| `aborted` | 0 | The caller cancelled (React Query unmount) |
| `invalid_response` | *n* | A 2xx body that did not parse as JSON |
| `unknown_error` | 0 | Anything thrown that is not already an `ApiError` |

If the response body is not the envelope, `ApiClient` falls back to a
status-derived code (400 → `validation_error`, 404 → `not_found`, …) and keeps
the body text as the message, truncated to 500 characters. Do not write UI that
depends on that path: it is a safety net for proxies and misrouted requests.
Note that `STATUS_CODE_FALLBACK` in `api-client.ts` and `_status_code_to_code`
in `backend/app/core/exceptions.py` are deliberately not the same table: they
agree on 401, 403, 404, 409, 422 and 429, but on 400 the client says
`validation_error` and the server says `bad_request`. The two only ever meet on
a response that is already outside the contract.

---

## Validation errors

A 422 always has `details.errors`, a list. Each entry:

| Key | Always present | Meaning |
| --- | --- | --- |
| `field` | yes | Dotted path to the offending field, with the `body` prefix removed. A non-field error is reported as `"body"` |
| `message` | yes | Human-readable description of this one failure |
| `type` | yes | Pydantic error kind, e.g. `string_too_short`, `string_pattern_mismatch`, `missing` |
| `context` | no | Extra Pydantic context, stringified so it stays JSON-safe. The raw `error` key is dropped |

Every entry is a single problem; a payload with two bad fields yields two entries,
and `message` stays the generic sentence. Map `details.errors[].field` onto form
inputs and render `details.errors[].message` next to them.

This contract is load-bearing, not aspirational: `login-page.tsx` and
`register-page.tsx` in `frontend/src` both flatten `details.errors[]` into
`{field: message}` and render the result under the `email` and `password`
inputs. Both drop entries whose `field` is `"body"` (not field-scoped) and keep
the first message when a field repeats. So a new endpoint that returns a 422
should keep the dotted-path `field` aligned with its own request body, and a new
form should follow that page's flattening rather than re-deriving paths from the
top-level `message`.

Do not parse `message` of the envelope for field information — it carries none.
`ApiError.fieldErrors` in `frontend/src/lib/api-client.ts` returns `details`, which
is the whole `{"errors": […]}` object.

---

## Request correlation (`X-Request-ID`)

`RequestContextMiddleware` in `backend/app/core/middleware.py` is the
**outermost** layer — above Starlette's `ServerErrorMiddleware`, not merely the
outermost user middleware. It cannot be installed with `app.add_middleware`,
which can only insert *inside* `ServerErrorMiddleware`; `add_request_context_middleware`
overrides `build_middleware_stack` (via `_install_outermost`) so the layer wraps
the entire stack, and defers the build to the first request so handlers and
routes registered afterwards are still included. It:

1. Resolves a correlation id, in priority order `X-Request-ID`,
   `X-Correlation-ID`, `X-Trace-ID`, else a fresh `uuid4()`. An inbound value is
   attacker-controlled, so it is stripped and truncated to 128 characters.
2. Binds it to a contextvar, so **every** log line emitted while handling the
   request carries it.
3. Stamps `X-Request-ID` onto the outgoing `http.response.start` ASGI message —
   so it is attached to whatever response is produced, including the 500 that
   `ServerErrorMiddleware` renders *below* it.
4. Reads the status back off that same message (defaulting to 500 if no response
   ever started) and emits exactly one `request_completed` access line with
   method, path, status, `duration_ms`, client IP, query and user agent.

Rules for callers:

- Send `X-Request-ID` (or `X-Correlation-ID`) to have your own id adopted, then
  quote the echoed value in a bug report.
- The header is safe to read cross-origin: it is listed in CORS `expose_headers`.
- **The header is present on every response, including a 5xx, and always equals
  the body's `request_id`.** Because the layer sits above `ServerErrorMiddleware`,
  it observes the failure response too; a client can correlate a 500 from the
  header alone, without having to parse the body. (`resolve_request_id` in
  `backend/app/core/exceptions.py` reads the contextvar first and falls back to
  the id stashed on `request.state`, because the contextvar is unbound by the
  time the catch-all handler runs.)
- The field is also a key in the JSON log lines, so
  `docker compose logs backend | grep <id>` finds the whole request.

Corollary for anyone adding middleware: a new layer added with
`app.add_middleware` sits *inside* `ServerErrorMiddleware` and therefore cannot
stamp a header onto a 500. If a layer must observe unhandled failures, it has to
be installed the way `add_request_context_middleware` does it.

Log levels follow the outcome: 5xx → ERROR, 4xx or `duration_ms >=
SLOW_REQUEST_MS` (default 1000) → WARNING, otherwise INFO. `LOG_REQUEST_BODY=true`
adds a redacted, 2000-character-truncated body preview to the access line; leave
it off outside debugging.

---

## Authentication

Bearer JWT, HS256, signed with `SECRET_KEY`. Issued by
`app/core/security.py`, policed by `app/services/auth_service.py`.

### Token shape

| Claim | Meaning |
| --- | --- |
| `sub` | The user id (UUID string) |
| `type` | `access` or `refresh` — the claim that stops a refresh token being replayed as a bearer credential |
| `iat` / `nbf` | Issued-at / not-before, UTC |
| `exp` | Expiry, UTC |
| `jti` | Token id; the key the revocation denylist is built on |

`exp`, `sub` and `type` are required on decode. A token missing any of them is
rejected, not tolerated. `sub`/`type`/`exp` are owned by the security module and
cannot be overridden by caller-supplied claims.

| Token | Lifetime | Source |
| --- | --- | --- |
| Access | 60 minutes | `ACCESS_TOKEN_EXPIRE_MINUTES` |
| Refresh | 7 days | `REFRESH_TOKEN_EXPIRE_DAYS` |

The `TokenPair` body is `{access_token, refresh_token, token_type: "bearer",
expires_in}` where `expires_in` is the access lifetime **in seconds**. The refresh
lifetime is not in the body; derive the refresh deadline from `REFRESH_TOKEN_EXPIRE_DAYS`.

### Flow rules

- **Login** returns a pair for an active account. Any credential failure returns
  the identical message — "Incorrect email or password." — so probing cannot
  distinguish an unknown address from a wrong password.
- **Refresh is single-use.** The presented token's `jti` is denylisted before the
  new pair is issued, so a replay fails with 401 `unauthorized`.
- **Type confusion is an error.** An access token presented to `/auth/refresh`
  (or a refresh token to `/auth/me`) is 401 `unauthorized` with a message naming
  the required type.
- **Logout revokes what it is given — so a client must send the refresh token.**
  `POST /auth/logout` takes two independent, both-optional inputs: a
  `TokenRefresh` body (`{"refresh_token": "…"}`) and an optional
  `Authorization: Bearer …` header. Each token it receives has its `jti`
  denylisted; anything it does not receive stays valid. A bearer header alone
  revokes **only the access token** — the refresh token survives until it expires
  in seven days, and a client holding it can mint a new pair. **A client that
  wants logout to end the session must send `{"refresh_token": "…"}` in the
  body**, alongside the header if it also wants the access token revoked.
  Sending neither is legal and revokes nothing.
- The response is 204 with an empty body whenever the request itself is
  well-formed. A token *string* that is unparseable or already expired is
  ignored, not reported — `AuthService.revoke` swallows the `UnauthorizedError`
  from decoding, so logout never fails the caller. A malformed *body* is still
  schema-validated like any other, and is a 422.
- **After logout the access token is still cryptographically valid**, but
  `GET /auth/me` rejects it with 401 because the dependency resolves identity
  through `AuthenticatedUser` (`app/api/deps.py`), which checks the denylist. A
  new endpoint opts into that same check by depending on `AuthenticatedUser`
  rather than `CurrentUser`.
- **Passwords are bcrypt, cost 12**, salted per hash; a corrupt or missing stored
  hash fails the check rather than raising, so one bad row cannot turn a login
  into a 500.

### 401 versus 403

401 means "who are you is unknown or unproven" — absent header, bad signature,
expired, wrong type, unknown subject, inactive account, revoked token. 403 means
"known, not permitted" — currently only the superuser check. Every 401 from a
domain error carries `WWW-Authenticate: Bearer`.

### Revocation store, and its limits

`RevocationStore` is an in-process `dict` keyed by `jti`, purged on lookup, and
expiring entries when the token would have expired anyway. Consequences to design
around:

- A restart clears it. Logout is not durable across a process restart.
- It is not shared between processes. The stack runs one backend worker, so this
  is correct today; a second worker needs Redis (the interface is deliberately
  narrow for exactly that).
- The frontend calls logout best-effort and clears local state regardless, so a
  failed logout never traps the user in a signed-in shell. It presents **both**
  tokens — `logoutRequest` in `frontend/src/services/auth.ts` puts the refresh
  token in the body and passes the access token as an explicit `Authorization`
  header, and sends `{ auth: false }` so an already-expired access token cannot
  trip the client's 401 recovery and rotate the very refresh token the call is
  revoking.

---

## CORS and response headers

Configured in `backend/app/main.py` from `CORS_ORIGINS` (comma-separated, no
trailing slashes; default `http://localhost:5173,http://127.0.0.1:5173`):

| Setting | Value |
| --- | --- |
| `allow_origins` | Parsed from `CORS_ORIGINS`; empty list disables the middleware entirely |
| `allow_credentials` | `True` |
| `allow_methods` / `allow_headers` | `*` |
| `expose_headers` | `["X-Request-ID"]` |

There are no other custom response headers, and no rate-limit or retry-after
headers, because there is no rate limiter.

### Same-origin by default

The Vite dev server (`:5173`) and `vite preview` (`:4173`) proxy `/api` and
`/health` to the backend, so the browser can call the API same-origin and CORS
never enters the picture. The proxy target is `VITE_DEV_PROXY_TARGET`, default
`http://localhost:8000`, set to `http://backend:8000` by Docker Compose.

The shipped `.env` sets `VITE_API_BASE_URL=http://localhost:8000/api/v1`, which
makes the client call the backend cross-origin instead and exercises the CORS
path. Either works; set the variable to `/api/v1` to go through the proxy.

---

## Pagination

`Page[T]` and `PageMeta` in `backend/app/schemas/common.py` are the reserved
envelope for the list endpoints of later phases. **No endpoint serves it yet** —
the catalogue above is the complete set, and none of it paginates.

```json
{
  "items": [],
  "meta": { "total": 0, "limit": 50, "offset": 0 }
}
```

| Field | Rule |
| --- | --- |
| `items` | The page's rows, in a deterministic order |
| `meta.total` | Total rows matching the filter, `>= 0` — the count without the page applied |
| `meta.limit` | Page size actually applied, `>= 1` |
| `meta.offset` | Rows skipped, `>= 0` |

Rules for the endpoints that adopt it:

- Parameters are `limit` and `offset`; the count always reflects the filters, not
  the page. Keep `limit >= 1` and cap it server-side — never honour an unbounded
  `limit` straight from the client.
- Always order by something unique, with a stable tiebreaker, or `total` and
  pagination will disagree with the rows returned.
- Filtering, sorting and search arrive as additional query parameters; keep the
  pagination parameters themselves fixed.
- Return the envelope directly — do not wrap it again.

`Message` (a `{"message": "…"}` acknowledgement body) is declared in the same
module for endpoints that acknowledge an action and have nothing else to return.
It is unused today; the two endpoints that would justify it (`logout`, and any
future delete) return `204` instead, which is the stronger signal. Prefer `204`.

---

## Health semantics

Two endpoints, two jobs. Do not merge them and do not move readiness to
`/health`.

| | `GET /health` | `GET /api/v1/health` |
| --- | --- | --- |
| Purpose | Liveness | Readiness + metadata |
| Touches the database | Never | `SELECT 1`, timed |
| Body | `{"status":"ok"}` | Full report, below |
| Status when the DB is down | 200 | 200 with `status: "degraded"` |

```json
{
  "status": "healthy",
  "app": "NEXUS",
  "version": "0.1.0",
  "environment": "development",
  "database": { "status": "connected", "latency_ms": 1.83 },
  "uptime_seconds": 128.44,
  "timestamp": "2026-01-01T00:00:00.000Z"
}
```

`status` is `healthy` when the probe succeeds and `degraded` when it fails;
`database.status` is `connected` or `unavailable`; `uptime_seconds` comes from
`time.monotonic()` since process start.

**A degraded database is still a 200.** The endpoint reporting that the service is
degraded is itself healthy, and the frontend's health card reads
`status`/`database.status` from a 200 body. Returning 503 here would make every
client treat a database outage as a server outage, and would let a dependency
failure drive a restart loop.

---

## The client contract

Everything a caller needs lives in three frontend places. Keep them in lockstep
with the backend schemas; a mismatch is a runtime failure, not a compile error.

| File | Role |
| --- | --- |
| `frontend/src/lib/api-client.ts` | `ApiClient`: base URL, bearer injection, query building, 30 s timeout, `ApiError` |
| `frontend/src/services/*.ts` | One function per endpoint (`auth.ts`, `health.ts`) — no fetch logic elsewhere |
| `frontend/src/types/api.ts` | Wire types mirroring the Pydantic models |

Client behaviour worth relying on:

- **Every failure is an `ApiError`** with `status`, `code`, `message`, `details`,
  `requestId` and convenience getters (`isUnauthorized`, `isValidationError`, …).
  Normalise anything else through `toApiError` from `services/errors.ts` before
  rendering it.
- **The token is injected, not passed.** The client calls a `TokenGetter` supplied
  by the auth store; pass `{ auth: false }` for public endpoints so no bearer
  header is sent to `login`, `register`, `refresh` or `health`.
- **The body `request_id` wins** over the header when both are present.
- **204 and `parse: 'none'` return `undefined`**, not `{}`.
- **Query values that are `null` or `undefined` are dropped**, not serialised as
  the strings `"null"` / `"undefined"`.
- **Absolute paths bypass the base URL.** `apiClient.get('http://…')` is passed
  through unchanged; every other path is resolved against the base.

`ApiErrorCode` is a closed union of today's codes widened with `(string & {})`, so
a new backend code type-checks without a frontend change but will not autocomplete.

---

## Checklist for a new endpoint

Router (`app/api/v1/<module>.py`) — thin, no business rules, no SQL:

- [ ] Declared on a router with `prefix` and `tags`; registered in
      `app/api/v1/router.py`.
- [ ] `response_model` is a dedicated `…Read` schema, not the ORM model.
- [ ] Explicit `status_code` (201 for creation) and a one-line `summary`.
- [ ] Identity comes from a dependency: `AuthenticatedUser` for endpoints that
      must honour revocation, `CurrentUser` for the rest, `SuperUser` for
      privileged ones. Never read the token in the handler.
- [ ] Write access goes through `Depends(get_*_service)`, which builds the
      request-scoped session → repository → service chain.
- [ ] No `HTTPException` and no hand-built error body. Raise `NotFoundError`,
      `ConflictError`, `ForbiddenError`, `ValidationError` from the service.

Service (`app/services/<module>_service.py`) — rules, no FastAPI imports:

- [ ] Raises a domain error for every failure mode; never returns `None` to mean
      "not found" from a public method (`UserService.get_by_id` is the pattern).
- [ ] Email/identifier normalisation and uniqueness are enforced here, not
      inferred from the ORM.
- [ ] A raw `IntegrityError` is translated to `ConflictError` so a race cannot
      leak a driver error.

Repository (`app/repositories/<module>.py`) — SQL only, no domain errors:

- [ ] Returns `None` for a miss and lets unexpected driver errors propagate.
- [ ] Uses the `User`-style mixins: application-side UUID primary key,
      `TimestampMixin` for `created_at` / `updated_at`.

Schemas (`app/schemas/<module>.py`):

- [ ] `…Read` uses `ConfigDict(from_attributes=True)`; no secret field appears.
- [ ] Constraints live here, so an invalid payload is a 422 with field details.
- [ ] Nullable fields default to `None` and are always serialised.
- [ ] List endpoints return `Page[ItemRead]`, not a bare array.

Cross-cutting:

- [ ] The endpoint is reachable under `/api/v1/…` and appears in Swagger with a
      useful summary.
- [ ] `frontend/src/services/` gains exactly one function, and
      `frontend/src/types/api.ts` gains the matching wire type.
- [ ] A test asserts the success shape *and* `assert_error_envelope` +
      `assert_no_internals` for every failure path. A path that can raise
      unhandled is exercised with `non_raising_client`, so the rendered 500 is
      asserted rather than re-raised.
- [ ] If it can return a 422, the request body fields are named in the schema so
      `details.errors[].field` addresses them the way a form does — the login
      and register pages already render that field path, and a new form will
      copy them.
- [ ] No 5xx path depends on its `detail` reaching the client; see
      [5xx is deliberately opaque](#5xx-is-deliberately-opaque).
- [ ] A migration exists for every schema change — `alembic revision
      --autogenerate -m "…"`, then confirm with `alembic check`.

---

## Worked example — the shape a future endpoint takes

**Not implemented.** Projects arrive in Phase 2; this is the pattern a first
list/create pair would follow, written against the real imports and the real
conventions.

Router:

```python
"""Project endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import get_project_service
from app.models.project import Project
from app.schemas.project import ProjectCreate, ProjectRead
from app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project",
)
async def create_project(
    payload: ProjectCreate,
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> Project:
    return await service.create(payload)
```

The imports are the point of the example, not decoration: `Project` (the ORM
model) is what the handler returns, `ProjectRead` is what leaves the process,
`ProjectService` names both the service and the annotation, and
`get_project_service` is the one dependency that builds the
session → repository → service chain. A router that annotates a service it did
not import, or imports an identity dependency it never uses, is the shape this
document exists to prevent.

Service — the only place a domain error is raised:

```python
async def create(self, data: ProjectCreate) -> Project:
    if await self.repository.exists_by_name(data.name):
        raise ConflictError("A project with this name already exists.")
    return await self.repository.create(name=data.name, description=data.description)
```

Response shapes to match:

```json
{
  "id": "6f1c…",
  "name": "NEXUS",
  "description": null,
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:00Z"
}
```

Note what the client receives on the duplicate: `409`,
`{"error":{"code":"conflict","message":"A project with this name already exists.","details":null,"request_id":"…"}}`
— no uniqueness detail that the caller could not have derived, and no traceback.

---

## Testing an endpoint

Backend tests live in `backend/tests/`. Run them from `backend/`:

```bash
# all 188 collected tests (needs the nexus_test database, created automatically)
python -m pytest

# the 145 database-free tests — 43 deselected
python -m pytest -m "not integration"

# one file
python -m pytest tests/test_errors.py -v
```

Use `backend/.venv/bin/python` (or `backend\.venv\Scripts\python.exe`) unless the
interpreter on your PATH already has the dependencies.

Two helpers and one client fixture in `backend/tests/conftest.py` and
`tests/test_errors.py` carry most of the contract. The first example is
illustrative — it uses the Phase 2 `projects` resource:

```python
async def test_duplicate_project_name(client, assert_error_envelope):
    await client.post("/api/v1/projects", json={"name": "NEXUS"})

    response = await client.post("/api/v1/projects", json={"name": "NEXUS"})

    error = assert_error_envelope(response, status_code=409, code="conflict")
    assert error["request_id"] == response.headers["X-Request-ID"]
```

- `assert_error_envelope(response, status_code=…, code=…)` asserts the exact key
  set, the code, a non-empty message, and that the body's `request_id` matches the
  response header.
- `assert_no_internals(response)` in `tests/test_errors.py` fails the test if the
  body leaked a traceback, driver name, SQL keyword or internal package path.
  Use it on every failure path, not just one.
- `non_raising_client` in `backend/tests/conftest.py` builds the app's
  `AsyncClient` with `ASGITransport(raise_app_exceptions=False)`, so an
  exception that escapes a handler is observed as the **rendered 500** instead of
  being re-raised inside the test. It is the only way to assert on the 5xx
  envelope itself — the default `httpx` behaviour re-raises and the response the
  user would have received is never visible. Use it whenever a test deliberately
  provokes a failure; use `offline_client` or `client` everywhere else. Like the
  others it depends on `app` alone, so a database-backed test can add
  `truncated_database` to its signature:

  ```python
  async def test_unhandled_failure_is_opaque(non_raising_client, assert_error_envelope, assert_no_internals):
      response = await non_raising_client.get("/api/v1/…")  # a route that raises

      error = assert_error_envelope(response, status_code=500, code="internal_error")
      assert error["message"] == "An internal server error occurred."
      assert error["details"] is None
      assert_no_internals(response)
  ```

  That last pair of assertions is the machine-readable form of
  [5xx is deliberately opaque](#5xx-is-deliberately-opaque); any new 5xx path
  should carry them.

Conventions that the suite encodes:

- A test that touches the database is marked `integration`; anything that can run
  without one should use `offline_client` and stay unmarked. The
  `pytest -m "not integration"` subset must pass with PostgreSQL stopped.
- The schema under test comes from Alembic, never from `Base.metadata.create_all`
  — a schema built from the models would prove nothing about the migration.
- Frontend: `npm test` from `frontend/` (30 tests, 10 files) runs Vitest with React
  Testing Library; `npm run typecheck` and `npm run lint` must also be clean.

**What is verified and what is not.** The 145/188 backend split and the frontend
counts above were produced by running the suites; PostgreSQL was not available in
that environment, so the 43 deselected `integration` tests have **not** been
executed, and nothing in this document that requires a live database — the
`nexus_test` creation path, the Alembic upgrade, or `docker-compose.yml` itself —
has been run against a running server.

---

## Known gaps

Accurate as of Phase 1. Each is a deliberate omission, not a bug to route around:

- **Error responses are absent from the OpenAPI schema.** Operations declare only
  their success codes, so Swagger UI does not render the envelope. Fix by adding
  a shared `responses={...}` model and referencing it from each route.
- **`rate_limited` is a reserved code with no implementation.** There is no rate
  limiter, so nothing can return 429. `bad_request` is likewise never raised
  deliberately, but it is *reachable*: `_status_code_to_code` returns it for any
  unmapped 4xx, so it is the code a 413 or 415 will carry.
- **`Page` and `Message` are declared but unserved.** No endpoint returns either
  shape today.
- **`frontend/src/types/pagination.ts` disagrees with the backend envelope, and
  the frontend type is the side that is wrong.** It still declares a flat
  `Paginated<T>` — `{items, total, limit, offset}` — while `Page[T]` in
  `backend/app/schemas/common.py` nests the counters under `meta`
  (`{items, meta: {total, limit, offset}}`), as shown above. The backend shape
  is the intended contract; the TypeScript interface needs the `meta` wrapper
  before the first list endpoint exists. Nothing is broken today *only* because
  no endpoint serves `Page[T]` yet — the two cannot be compared at runtime, so
  the mismatch is invisible until the first paginated endpoint lands. Fix the
  type then, or before, and re-check both sides against this section.
- **The revocation denylist is in-process** — not restart-durable, not shared
  between workers.
- **No idempotency keys, no ETags, no cursor pagination, no bulk endpoints.** The
  first two phases do not need them; add them as real use cases appear rather than
  as speculative machinery.
- **`GET /api/v1/health` is unauthenticated**, which is correct for a readiness
  probe on a local-first, single-user system bound to loopback. It exposes
  `environment`, `version` and uptime and nothing else.
