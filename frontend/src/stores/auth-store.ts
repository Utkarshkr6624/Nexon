import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import { ApiError, apiClient } from '@/lib/api-client'
import {
  fetchCurrentUser,
  loginRequest,
  logoutRequest,
  refreshRequest,
  registerRequest,
} from '@/services/auth'
import { toApiError } from '@/services/errors'
import type { LoginPayload, RegisterPayload, User } from '@/types'

export const AUTH_STORAGE_KEY = 'nexus.auth'

/**
 * `initializing` covers the window where a persisted session is being verified
 * against the backend. Guards use it to avoid bouncing a signed-in user to
 * /login on every reload.
 */
export type AuthStatus = 'initializing' | 'authenticated' | 'anonymous'

const ANONYMOUS = {
  accessToken: null,
  refreshToken: null,
  user: null,
  status: 'anonymous',
} as const

interface AuthState {
  accessToken: string | null
  refreshToken: string | null
  user: User | null
  status: AuthStatus
  /** A login/register/logout call is in flight. */
  pending: boolean
  /** Last auth failure, rendered inline by the login and register forms. */
  error: ApiError | null
  login: (payload: LoginPayload) => Promise<void>
  register: (payload: RegisterPayload) => Promise<void>
  logout: () => Promise<void>
  /**
   * Verifies a persisted session against `GET /auth/me`. Single-flight: the
   * boot effect and any later caller share one verification, so a StrictMode
   * double-mount cannot race the single-use refresh token.
   */
  hydrate: () => Promise<void>
  /**
   * Renews an expired session on behalf of the API client and returns the fresh
   * access token, or null when it could not be renewed. Only a refresh token
   * the backend rejects ends the session; a backend we could not reach leaves
   * the stored pair in place for the next attempt.
   */
  renewAccessToken: () => Promise<string | null>
  clearError: () => void
}

type SessionListener = () => void

const sessionListeners = new Set<SessionListener>()

/**
 * Subscribes to the moments where cached data must not outlive the session
 * that fetched it: a sign-out, a session the backend rejected, and a fresh
 * sign-in. The app layer registers the query cache here rather than the store
 * importing it, which would couple the store to the query client's mount.
 */
export function onSessionChange(listener: SessionListener): () => void {
  sessionListeners.add(listener)
  return () => {
    sessionListeners.delete(listener)
  }
}

function announceSessionChange(): void {
  for (const listener of [...sessionListeners]) listener()
}

/**
 * How a refresh attempt ended. `unreachable` is kept apart from `unusable`
 * because a backend that did not answer says nothing about the token: treating
 * it as a rejection would sign the user out over a network blip.
 */
type RefreshOutcome =
  | { kind: 'refreshed' }
  | { kind: 'unusable' }
  | { kind: 'unreachable'; error: ApiError }

/**
 * Module-scoped so they outlive any single store read. Refresh tokens are
 * single-use server-side, so a second concurrent rotation would fail and take
 * the winner's freshly stored pair down with it.
 */
let hydrateInFlight: Promise<void> | null = null
let refreshInFlight: Promise<RefreshOutcome> | null = null

function isUnreachable(error: ApiError): boolean {
  return error.isTransportError || error.isTimeout || error.status >= 500
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => {
      async function rotateRefreshToken(): Promise<RefreshOutcome> {
        const { refreshToken } = get()
        if (!refreshToken) return { kind: 'unusable' }
        try {
          const tokens = await refreshRequest(refreshToken)
          set({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token })
          return { kind: 'refreshed' }
        } catch (cause) {
          const error = toApiError(cause)
          return isUnreachable(error) ? { kind: 'unreachable', error } : { kind: 'unusable' }
        }
      }

      /** Single-flight: concurrent callers share one rotation of one token. */
      function refreshSession(): Promise<RefreshOutcome> {
        if (refreshInFlight) return refreshInFlight
        const attempt = rotateRefreshToken().finally(() => {
          refreshInFlight = null
        })
        refreshInFlight = attempt
        return attempt
      }

      function clearSession(): void {
        set({ ...ANONYMOUS, pending: false, error: null })
        announceSessionChange()
      }

      /**
       * The session is unverified rather than rejected — the backend was down,
       * slow or erroring. The pair is kept so the next verification can succeed
       * and the failure is reported as retryable instead of costing a sign-in.
       */
      function markUnreachable(error: ApiError): void {
        set({ status: 'anonymous', pending: false, error })
      }

      async function loadUser(): Promise<void> {
        const user = await fetchCurrentUser()
        set({ user, status: 'authenticated', pending: false, error: null })
      }

      async function renewForRequest(): Promise<string | null> {
        const outcome = await refreshSession()
        if (outcome.kind === 'refreshed') return get().accessToken
        if (outcome.kind === 'unusable') clearSession()
        return null
      }

      async function renewAndVerify(): Promise<void> {
        const outcome = await refreshSession()
        if (outcome.kind === 'unreachable') {
          markUnreachable(outcome.error)
          return
        }
        if (outcome.kind === 'unusable') {
          clearSession()
          return
        }
        try {
          await loadUser()
        } catch (cause) {
          const error = toApiError(cause)
          if (isUnreachable(error)) {
            markUnreachable(error)
            return
          }
          clearSession()
        }
      }

      async function verifyPersistedSession(): Promise<void> {
        const { accessToken, refreshToken } = get()
        if (!accessToken && !refreshToken) {
          clearSession()
          return
        }
        try {
          await loadUser()
        } catch (cause) {
          const error = toApiError(cause)
          // A 401 means the access token is spent; anything else — including a
          // timeout or a 5xx — says nothing about the session's validity.
          if (!error.isUnauthorized) {
            markUnreachable(error)
            return
          }
          await renewAndVerify()
        }
      }

      return {
        ...ANONYMOUS,
        status: 'initializing',
        pending: false,
        error: null,

        async login(payload) {
          set({ pending: true, error: null })
          try {
            const tokens = await loginRequest(payload)
            set({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token })
            await loadUser()
            announceSessionChange()
          } catch (cause) {
            // A pair we never exchanged for a user must not survive: it is
            // persisted, and the next verification would adopt it as a session.
            clearSession()
            set({ error: toApiError(cause) })
          }
        },

        async register(payload) {
          set({ pending: true, error: null })
          try {
            await registerRequest(payload)
            const tokens = await loginRequest({
              email: payload.email,
              password: payload.password,
            })
            set({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token })
            await loadUser()
            announceSessionChange()
          } catch (cause) {
            clearSession()
            set({ error: toApiError(cause) })
          }
        },

        async logout() {
          set({ pending: true })
          const { accessToken, refreshToken } = get()
          try {
            await logoutRequest(accessToken, refreshToken)
          } catch {
            // A failed revoke leaves a record on the server, but the local
            // session has to go either way.
          } finally {
            clearSession()
          }
        },

        hydrate() {
          if (hydrateInFlight) return hydrateInFlight
          const attempt = verifyPersistedSession().finally(() => {
            hydrateInFlight = null
          })
          hydrateInFlight = attempt
          return attempt
        },

        renewAccessToken: renewForRequest,

        clearError() {
          set({ error: null })
        },
      }
    },
    {
      name: AUTH_STORAGE_KEY,
      storage: createJSONStorage(() => window.localStorage),
      // `status` is deliberately not persisted: it is recomputed by `hydrate`.
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        user: state.user,
      }),
    },
  ),
)

/** Supplies the bearer token to the shared client for every authenticated call. */
apiClient.setTokenGetter(() => useAuthStore.getState().accessToken)

/**
 * Lets a 401 on an ordinary call renew the session once and replay the request
 * that hit it, so an expired access token no longer strands a signed-in user.
 */
apiClient.setUnauthorizedHandler(() => useAuthStore.getState().renewAccessToken())

export function selectIsAuthenticated(state: AuthState): boolean {
  return state.status === 'authenticated' && state.accessToken !== null
}

export function selectDisplayName(user: User | null): string {
  if (!user) return 'Guest'
  const name = user.full_name?.trim()
  return name && name.length > 0 ? name : user.email.split('@')[0] ?? user.email
}

export function selectInitials(user: User | null): string {
  const display = selectDisplayName(user)
  const parts = display.split(/[\s.@_-]+/).filter(Boolean)
  if (parts.length === 0) return '?'
  const initials = parts.slice(0, 2).map((part) => part.charAt(0).toUpperCase())
  return initials.join('')
}
