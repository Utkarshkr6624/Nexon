/**
 * Wire types for the NEXUS backend (`/api/v1`).
 *
 * These mirror the FastAPI/Pydantic response models exactly: field names are
 * snake_case and timestamps are ISO-8601 strings. Keep this file in lockstep
 * with the backend schemas — a mismatch here fails at runtime, not compile
 * time, because the response body is only typed by convention.
 */

/** ISO-8601 timestamp as emitted by Pydantic, e.g. `2026-01-01T00:00:00Z`. */
export type ISODateTimeString = string

/** UUID v4 string. */
export type UUIDString = string

/**
 * The authoritative role. `is_superuser` is gone from the wire: the role is now
 * the single answer to "what may this account do?", and publishing both invites
 * a client to branch on the flag the backend no longer consults.
 */
export type UserRole = 'user' | 'admin'

/**
 * Effective capabilities, computed server-side from `User.role` so the client
 * never re-implements the role → permission map. The `(string & {})` tail keeps
 * completion for the grants in use today while accepting capabilities added by
 * later backend phases.
 */
export type PermissionString =
  | 'users.read'
  | 'users.write'
  | 'projects.read'
  | 'projects.write'
  | 'tasks.read'
  | 'tasks.write'
  | 'analytics.read'
  | (string & {})

export interface User {
  id: UUIDString
  email: string
  username: string
  display_name: string | null
  avatar_url: string | null
  role: UserRole
  /** Effective permission strings, computed server-side from the role. */
  permissions: PermissionString[]
  is_active: boolean
  is_verified: boolean
  created_at: ISODateTimeString
  updated_at: ISODateTimeString
  last_login_at: ISODateTimeString | null
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: 'bearer'
  /** Access-token lifetime in seconds. */
  expires_in: number
  /**
   * The session these tokens belong to, so the client can tell "this device"
   * apart from the others without a second request. Optional on the wire only
   * so a token minted outside the session flow still validates; every login and
   * refresh sets it, and `null` means "unknown" — never "any device".
   */
  session_id: UUIDString
}

export interface RegisterPayload {
  username: string
  email: string
  password: string
  display_name?: string
}

export interface LoginPayload {
  email: string
  password: string
}

export interface RefreshPayload {
  refresh_token: string
}

/**
 * Partial update of the caller's own profile.
 *
 * Every field is optional and nullable: `null` clears the stored value, while
 * omitting the key leaves it untouched. Email and password are absent on
 * purpose — both are identity/credential transitions with rules (verification,
 * session revocation, audit rows) that a profile edit has no business
 * performing, so they have their own endpoints.
 */
export interface ProfileUpdatePayload {
  display_name?: string | null
  avatar_url?: string | null
  username?: string | null
}

/**
 * Confirmation for deleting an account.
 *
 * `password` re-authenticates the requester, because a bearer token left in a
 * shared browser's storage is enough to read an account but must not be enough
 * to destroy one. `confirm` exists because a password alone cannot tell "typed
 * into the form I was shown" from "replayed by a script that saw it last time".
 */
export interface UserDeletionPayload {
  password: string
  confirm: boolean
}

/** `current_password` is required even though the caller holds a valid token. */
export interface PasswordChangePayload {
  current_password: string
  new_password: string
}

/**
 * Ask for a reset link.
 *
 * The backend answers with an identical body whether or not the address is
 * registered, so this endpoint cannot be used to enumerate accounts — which is
 * why it must stay unauthenticated and why the UI must not branch on the answer.
 */
export interface PasswordResetRequestPayload {
  email: string
}

/** Redeem a reset token and set a new password. */
export interface PasswordResetConfirmPayload {
  token: string
  new_password: string
}

export interface PasswordResetRequestedResponse {
  accepted: boolean
  /**
   * Raw reset token, non-production only.
   *
   * NEXUS is local-first and ships no mail service, so there is no channel to
   * deliver a reset link through and the account could not be recovered at all
   * without it. It is a bearer credential for a full account takeover, which is
   * why the backend leaves it `null` whenever `settings.is_production` — the
   * field exists so the contract does not change shape between environments,
   * not because it is safe to render anywhere.
   */
  dev_token?: string | null
}

/**
 * One device sign-in, as shown in the sessions UI.
 *
 * Deliberately carries neither the token hash nor the user id: a digest of a
 * live refresh token is still a credential worth attacking, and repeating the
 * owner on every row tells the client nothing it does not already know — while
 * being exactly the kind of field that ends up rendered in a revoke prompt.
 */
export interface AuthSession {
  id: UUIDString
  user_agent: string | null
  ip_address: string | null
  created_at: ISODateTimeString
  last_used_at: ISODateTimeString | null
  expires_at: ISODateTimeString
  revoked_at: ISODateTimeString | null
  /** True for the session the listing request itself was made with. */
  is_current: boolean
}

export interface SessionListResponse {
  sessions: AuthSession[]
  /**
   * Stated once at the top level rather than left to be inferred from
   * `is_current`, so the client can compare a selected device in one pass.
   */
  current_id: UUIDString | null
}

/**
 * Stable machine-readable error codes. The union keeps editor completion for
 * the codes in use today while still accepting codes added by later backend
 * phases.
 */
export type ApiErrorCode =
  | 'validation_error'
  | 'not_found'
  | 'unauthorized'
  | 'forbidden'
  | 'conflict'
  | 'internal_error'
  | 'rate_limited'
  | (string & {})

/** Body of the `error` key in the backend's error envelope. */
export interface ApiErrorBody {
  code: ApiErrorCode
  /** Safe to render in the UI; never contains a stack trace, SQL or secret. */
  message: string
  /** Field-level context for `validation_error`; `null` otherwise. */
  details: Record<string, unknown> | null
  request_id: UUIDString
}

/** Every non-2xx response: `{"error": {...}}`. */
export interface ApiErrorEnvelope {
  error: ApiErrorBody
}

export type HealthStatus = 'healthy' | 'degraded'

export type DatabaseStatus = 'connected' | 'unavailable'

/** `GET /api/v1/health` — still 200 when the database is down, but `degraded`. */
export interface HealthResponse {
  status: HealthStatus
  app: string
  version: string
  environment: string
  database: {
    status: DatabaseStatus
    latency_ms: number
  }
  uptime_seconds: number
  timestamp: ISODateTimeString
}

/** `GET /health` — root liveness, never touches the database. */
export interface RootHealthResponse {
  status: 'ok'
}
