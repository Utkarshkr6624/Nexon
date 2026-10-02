import { KeyRound, ShieldAlert } from 'lucide-react'
import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'

import { EmptyState } from '@/components/feedback/empty-state'
import { ErrorState } from '@/components/feedback/error-state'
import { AuthShell } from '@/components/layout/auth-shell'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { PasswordField } from '@/features/auth/components/password-field'
import { usePasswordRules } from '@/features/auth/use-password-rules'
import { resetPasswordRequest } from '@/services/auth'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import type { ApiError } from '@/lib/api-client'

const QUIET_LINK =
  'text-foreground underline-offset-4 transition-colors duration-150 hover:text-primary hover:underline'

/**
 * A reset token is single-use and short-lived, and the backend answers 401
 * identically for unknown, spent, expired and not-a-reset-token. So a rejection
 * says exactly that much and sends the person back to the start; the server's
 * own message is not shown here because it would only restate the same fact.
 */
const LINK_DEAD_CODES = new Set([401, 409])

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()

  const token = searchParams.get('token')?.trim() ?? ''

  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [attempted, setAttempted] = useState(false)
  // The mismatch surfaces the moment the confirmation is left, and not before:
  // it is a fact this form can settle on its own, with no round trip, and a
  // password manager filling both fields deserves to see it as soon as it moves
  // on.
  const [confirmTouched, setConfirmTouched] = useState(false)
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const passwordRef = useRef<HTMLInputElement>(null)
  const confirmRef = useRef<HTMLInputElement>(null)

  const { allSatisfied: policyMet } = usePasswordRules(password)

  const mismatch = confirm.length > 0 && confirm !== password
  const confirmError = mismatch
    ? 'Passwords do not match.'
    : confirmTouched && confirm.length === 0
      ? 'Confirm your new password.'
      : undefined
  const passwordError = attempted && !policyMet ? 'Meet every requirement in the checklist.' : undefined

  const linkDead = failure !== null && LINK_DEAD_CODES.has(failure.status)
  const showBanner = failure !== null && !linkDead

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAttempted(true)

    if (!policyMet) {
      passwordRef.current?.focus()
      return
    }
    if (confirm.length === 0) {
      setConfirmTouched(true)
      confirmRef.current?.focus()
      return
    }
    if (mismatch) {
      confirmRef.current?.focus()
      return
    }

    setPending(true)
    setFailure(null)

    try {
      await resetPasswordRequest({ token, new_password: password })
      // No sign-in here on purpose: the backend revokes every session as part of
      // the reset — including any that were still open when the link was
      // requested — so the honest outcome is a clean trip through the login
      // screen rather than a session this page has no token for.
      toast.success('Password updated', 'Sign in with your new password.')
      navigate('/login', { replace: true })
    } catch (cause) {
      setFailure(toApiError(cause))
    } finally {
      setPending(false)
    }
  }

  if (token.length === 0) {
    return (
      <AuthShell
        title="Reset link incomplete"
        description="This link arrived without the token that proves which account it is for."
        footer={
          <>
            Remembered it?{' '}
            <Link to="/login" className={QUIET_LINK}>
              Back to sign in
            </Link>
          </>
        }
      >
        <Card>
          {/* No `CardHeader` — `AuthShell` already owns the page masthead. */}
          <CardContent className="pt-6">
            <EmptyState
              icon={KeyRound}
              title="This reset link is missing its token"
              description="The address probably lost its query string when it was copied, or the link was trimmed. Request a fresh one and open it exactly as it arrives."
              action={
                <Button asChild>
                  <Link to="/forgot-password">Request a new link</Link>
                </Button>
              }
            />
          </CardContent>
        </Card>
      </AuthShell>
    )
  }

  return (
    <AuthShell
      title="Choose a new password"
      description="You are signed out of every device once this goes through, including the ones you are not using right now."
      footer={
        <>
          Changed your mind?{' '}
          <Link to="/login" className={QUIET_LINK}>
            Back to sign in
          </Link>
        </>
      }
    >
      <Card>
        {/* No `CardHeader` — `AuthShell` already owns the page masthead — so the
            content keeps the card's own side padding instead of `pt-0`. */}
        <CardContent className="pt-6">
          {/*
            `noValidate` because the validation below is the one that runs:
            `minLength` would otherwise block submission with a native bubble
            before `onSubmit` ever fires, and the inline messages the form
            actually renders would never appear.
          */}
          <form onSubmit={onSubmit} noValidate className="app-form-stack">
            {linkDead && failure ? (
              <div
                role="alert"
                className="flex flex-col gap-3 rounded-lg border border-destructive/30 bg-destructive/[0.04] p-4"
              >
                <div className="flex items-start gap-3">
                  <ShieldAlert
                    className="mt-0.5 size-4 shrink-0 text-destructive"
                    aria-hidden="true"
                  />
                  <div className="min-w-0 space-y-1">
                    <p className="text-sm font-medium text-foreground">
                      That reset link is no longer valid
                    </p>
                    <p className="text-sm leading-relaxed text-muted-foreground">
                      Reset links can be redeemed once and expire quickly. A spent link is
                      indistinguishable from a mistyped one, so this page cannot tell you which
                      it was — request a fresh one and it will arrive valid.
                    </p>
                  </div>
                </div>
                <div>
                  <Button asChild variant="outline" size="sm">
                    <Link to="/forgot-password">Request a new link</Link>
                  </Button>
                </div>
              </div>
            ) : (
              showBanner &&
              failure && <ErrorState error={failure} compact title="Could not set the password" />
            )}

            <PasswordField
              id="new_password"
              label="New password"
              value={password}
              onChange={setPassword}
              inputRef={passwordRef}
              autoComplete="new-password"
              placeholder="Something you have not used here before"
              disabled={pending}
              error={passwordError}
              showStrength
              showRules
            />

            <PasswordField
              id="confirm_password"
              label="Confirm new password"
              value={confirm}
              onChange={setConfirm}
              inputRef={confirmRef}
              autoComplete="new-password"
              placeholder="Type it again"
              disabled={pending}
              error={confirmError}
              onBlur={() => setConfirmTouched(true)}
            />

            <div className="flex flex-col gap-3">
              <Button type="submit" className="w-full" disabled={pending} aria-busy={pending}>
                {pending && <Spinner size="sm" label="Updating your password" />}
                {pending ? 'Updating…' : 'Update password'}
              </Button>
              <p className="app-form-hint">
                Setting a new password revokes every session on this account and writes the new
                one to this machine only.
              </p>
            </div>
          </form>
        </CardContent>
      </Card>
    </AuthShell>
  )
}
