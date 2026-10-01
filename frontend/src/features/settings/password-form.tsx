import { useId, useState } from 'react'
import type { FormEvent } from 'react'
import { Check, Eye, EyeOff, Info } from 'lucide-react'

import { Alert, AlertDescription, AlertIcon, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Spinner } from '@/components/ui/spinner'
import { ErrorState } from '@/components/feedback/error-state'
import { STRENGTH_TOKENS } from '@/features/auth/password-strength'
import { usePasswordRules } from '@/features/auth/use-password-rules'
import { toApiError } from '@/services/errors'
import type { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { toast } from '@/stores/toast-store'

interface PasswordFieldProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  autoComplete: 'current-password' | 'new-password'
  error?: string | null
  /** Renders the strength meter and the live policy checklist. */
  withFeedback?: boolean
  optional?: boolean
  autoFocus?: boolean
}

/**
 * Password control with a reveal toggle and, for a *new* password, the live
 * policy checklist. The reveal button is a real focusable control rather than a
 * `::-webkit` trick so it works with a keyboard and announces itself.
 */
export function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  error = null,
  withFeedback = false,
  optional = false,
  autoFocus = false,
}: PasswordFieldProps) {
  const [revealed, setRevealed] = useState(false)
  const { results, strength } = usePasswordRules(value)
  const tokens = STRENGTH_TOKENS[strength.level]
  const errorId = `${id}-error`

  return (
    <div className="app-form-field">
      <Label htmlFor={id} optional={optional}>
        {label}
      </Label>
      <Input
        id={id}
        type={revealed ? 'text' : 'password'}
        autoComplete={autoComplete}
        autoFocus={autoFocus}
        spellCheck={false}
        autoCapitalize="none"
        value={value}
        error={Boolean(error)}
        onChange={(event) => onChange(event.target.value)}
        aria-describedby={error ? errorId : undefined}
        endAdornment={
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 text-muted-foreground hover:text-foreground"
            aria-label={revealed ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
            aria-pressed={revealed}
            onClick={() => setRevealed((previous) => !previous)}
          >
            {revealed ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
          </Button>
        }
      />

      {error ? (
        <p id={errorId} className="app-form-error">
          {error}
        </p>
      ) : null}

      {withFeedback && !error ? (
        <div className="space-y-2 pt-0.5">
          {value.length > 0 && (
            <div className="flex items-center gap-2">
              <Progress
                value={strength.score}
                aria-label="Password strength"
                indicatorClassName={tokens.bar}
              />
              <span className={`w-12 shrink-0 text-xs font-medium ${tokens.text}`}>
                {tokens.label}
              </span>
            </div>
          )}

          <ul className="grid gap-1 sm:grid-cols-2">
            {results.map((result) => (
              <li
                key={result.id}
                className={
                  result.satisfied
                    ? 'flex items-center gap-1.5 text-xs text-foreground'
                    : 'flex items-center gap-1.5 text-xs text-muted-foreground'
                }
              >
                {result.satisfied ? (
                  <Check className="size-3.5 shrink-0 text-success" aria-hidden="true" />
                ) : (
                  <span
                    aria-hidden="true"
                    className="size-1.5 shrink-0 rounded-full bg-border"
                  />
                )}
                <span className="truncate">{result.label}</span>
                <span className="sr-only">{result.satisfied ? '— met' : '— not met yet'}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

/**
 * Current password, new password, confirmation. Changing a password is a
 * credential transition rather than a profile edit: the backend revokes every
 * other session as part of it, which the copy states outright so nobody is
 * surprised by a sign-out on a device they are still using.
 */
export function PasswordForm() {
  const changePassword = useAuthStore((state) => state.changePassword)
  const clearError = useAuthStore((state) => state.clearError)

  const formId = useId()
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [currentError, setCurrentError] = useState<string | null>(null)
  const [confirmError, setConfirmError] = useState<string | null>(null)

  const { allSatisfied: policyMet } = usePasswordRules(newPassword)

  const confirmProblem =
    confirmPassword.length > 0 && confirmPassword !== newPassword
      ? 'The two passwords do not match.'
      : null

  const ready =
    currentPassword.length > 0 && newPassword.length > 0 && policyMet && confirmProblem === null

  function edit(setter: (value: string) => void) {
    return (value: string) => {
      setter(value)
      setCurrentError(null)
      setConfirmError(null)
      clearError()
      setFailure(null)
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!ready || busy) return

    setBusy(true)
    setFailure(null)
    setCurrentError(null)
    setConfirmError(null)
    clearError()

    try {
      await changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      })

      const error = useAuthStore.getState().error
      if (error) {
        setFailure(error)
        // A wrong current password is a field problem, not a panel problem, and
        // the backend says so in its own words.
        if (error.isUnauthorized || error.status === 400) {
          setCurrentError('That is not your current password.')
        }
        return
      }

      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      toast.success('Password changed', 'Other devices have been signed out.')
    } catch (cause) {
      const error = toApiError(cause)
      setFailure(error)
      if (error.isUnauthorized || error.status === 400) {
        setCurrentError('That is not your current password.')
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Change password</CardTitle>
        <CardDescription>
          Pick something you have not used here before. NEXUS checks the policy in your browser
          as you type and the server checks it again on save.
        </CardDescription>
      </CardHeader>
      <CardContent className="app-form-stack">
        <Alert>
        <AlertIcon>
          <Info />
        </AlertIcon>
        <div className="min-w-0">
          <AlertTitle>Other devices will be signed out</AlertTitle>
          <AlertDescription>
            The backend revokes every other session when the password changes. This browser
            stays signed in; anything else you have open will ask for the new password.
          </AlertDescription>
        </div>
      </Alert>

      <form onSubmit={onSubmit} className="app-form-stack max-w-xl" noValidate>
        {failure && <ErrorState error={failure} compact />}

        <PasswordField
          id={`${formId}-current`}
          label="Current password"
          autoComplete="current-password"
          value={currentPassword}
          onChange={edit(setCurrentPassword)}
          error={currentError}
          autoFocus
        />

        <PasswordField
          id={`${formId}-new`}
          label="New password"
          autoComplete="new-password"
          value={newPassword}
          onChange={edit(setNewPassword)}
          withFeedback
        />

        <PasswordField
          id={`${formId}-confirm`}
          label="Confirm new password"
          autoComplete="new-password"
          value={confirmPassword}
          onChange={edit(setConfirmPassword)}
          error={confirmProblem ?? confirmError}
        />

        <div className="flex items-center gap-3">
          <Button type="submit" disabled={!ready || busy}>
            {busy && <Spinner size="sm" />}
            {busy ? 'Changing…' : 'Change password'}
          </Button>
          {!ready && newPassword.length > 0 && !policyMet && (
            <p className="text-xs text-muted-foreground">Meet every rule above to continue.</p>
          )}
        </div>
      </form>
      </CardContent>
    </Card>
  )
}
