import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ErrorState } from '@/components/feedback/error-state'
import { AuthShell } from '@/components/layout/auth-shell'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Spinner } from '@/components/ui/spinner'
import type { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'

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

export default function RegisterPage() {
  const navigate = useNavigate()
  const register = useAuthStore((state) => state.register)
  const pending = useAuthStore((state) => state.pending)
  const error = useAuthStore((state) => state.error)
  const clearError = useAuthStore((state) => state.clearError)

  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const fieldErrors = error ? fieldErrorMessages(error) : {}
  const emailError = fieldErrors.email
  const passwordError = fieldErrors.password

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await register({
      email,
      password,
      ...(fullName.trim() ? { full_name: fullName.trim() } : {}),
    })
    if (useAuthStore.getState().status === 'authenticated') {
      navigate('/dashboard', { replace: true })
    }
  }

  return (
    <AuthShell
      title="Create your workspace"
      description="One account per browser profile in Phase 1. Nothing leaves this machine."
      footer={
        <>
          Already registered?{' '}
          <Link to="/login" className="font-medium text-foreground hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <Card>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            {error && <ErrorState error={error} compact />}

            <div className="space-y-2">
              <Label htmlFor="full_name">Full name</Label>
              <Input
                id="full_name"
                autoComplete="name"
                value={fullName}
                onChange={(event) => setFullName(event.target.value)}
                placeholder="Optional"
              />
            </div>

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
                autoComplete="new-password"
                required
                minLength={8}
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value)
                  clearError()
                }}
                placeholder="At least 8 characters"
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
              {pending ? 'Creating account…' : 'Create account'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </AuthShell>
  )
}