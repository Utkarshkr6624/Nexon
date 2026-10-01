import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

import { ErrorState } from '@/components/feedback/error-state'
import { AuthShell } from '@/components/layout/auth-shell'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Spinner } from '@/components/ui/spinner'
import type { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'

interface LocationState {
  from?: string
}

/**
 * Flattens the `details.errors[]` list of a 422 envelope into `{ field: message }`,
 * per `docs/api-conventions.md`. Entries that are not field-scoped (`field: "body"`)
 * are dropped, and the first message wins when a field repeats.
 */
function fieldErrorMessages(error: ApiError): Record<string, string> {
  const { errors } = error.fieldErrors
  if (!Array.isArray(errors)) return {}

  const messages: Record<string, string> = {}
  for (const entry of errors as Array<{ field?: unknown; message?: unknown }>) {
    const field = entry?.field
    const message = entry?.message
    if (typeof field !== 'string' || typeof message !== 'string') continue
    if (field === '' || field === 'body' || field in messages) continue
    messages[field] = message
  }
  return messages
}

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const login = useAuthStore((state) => state.login)
  const pending = useAuthStore((state) => state.pending)
  const error = useAuthStore((state) => state.error)
  const clearError = useAuthStore((state) => state.clearError)

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const fieldErrors = error ? fieldErrorMessages(error) : {}
  const emailError = fieldErrors.email
  const passwordError = fieldErrors.password

  const redirectTo = (location.state as LocationState | null)?.from ?? '/dashboard'

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await login({ email, password })
    // Only navigate on success; a failed attempt leaves the store's error to render.
    if (useAuthStore.getState().status === 'authenticated') {
      navigate(redirectTo, { replace: true })
    }
  }

  return (
    <AuthShell
      title="Sign in to NEXUS"
      description="Your workspace is local-first. Credentials are exchanged for a JWT pair held in this browser only."
      footer={
        <>
          No account yet?{' '}
          <Link to="/register" className="font-medium text-foreground hover:underline">
            Create one
          </Link>
        </>
      }
    >
      <Card>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            {error && <ErrorState error={error} compact />}

            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => {
                  setEmail(event.target.value)
                  clearError()
                }}
                placeholder="you@nexus.local"
                aria-invalid={Boolean(emailError)}
                aria-describedby={emailError ? 'email-error' : undefined}
              />
              {emailError && (
                <p id="email-error" className="text-xs text-destructive">
                  {emailError}
                </p>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value)
                  clearError()
                }}
                placeholder="••••••••••"
                aria-invalid={Boolean(passwordError)}
                aria-describedby={passwordError ? 'password-error' : undefined}
              />
              {passwordError && (
                <p id="password-error" className="text-xs text-destructive">
                  {passwordError}
                </p>
              )}
            </div>

            <Button type="submit" className="w-full" disabled={pending}>
              {pending && <Spinner size="sm" />}
              {pending ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </AuthShell>
  )
}