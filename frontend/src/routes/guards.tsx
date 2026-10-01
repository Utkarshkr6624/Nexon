import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { Brand } from '@/components/brand/logo'
import { Spinner } from '@/components/ui/spinner'
import { useAuthStore } from '@/stores/auth-store'

/**
 * Shown while a persisted session is being verified. It is deliberately
 * branded and silent — the alternative is a flash of the login screen on every
 * reload for a signed-in user.
 *
 * Exported so the auth route boundary can reuse it for the lazy chunks behind
 * /login instead of keeping a near-identical private copy.
 */
export function BootScreen() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background">
      <Brand />
      <Spinner label="Restoring session" />
    </div>
  )
}

export interface RequireAuthProps {
  children: ReactNode
}

/** Gate for the application shell. Remembers where the user was headed. */
export function RequireAuth({ children }: RequireAuthProps) {
  const status = useAuthStore((state) => state.status)
  const location = useLocation()

  if (status === 'initializing') return <BootScreen />

  if (status !== 'authenticated') {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />
  }

  return children
}

export interface RequireAnonymousProps {
  children: ReactNode
}

/** Keeps signed-in users out of the credential screens: /login, /register,
 * /forgot-password and /reset-password. */
export function RequireAnonymous({ children }: RequireAnonymousProps) {
  const status = useAuthStore((state) => state.status)

  if (status === 'initializing') return <BootScreen />
  if (status === 'authenticated') return <Navigate to="/dashboard" replace />

  return children
}