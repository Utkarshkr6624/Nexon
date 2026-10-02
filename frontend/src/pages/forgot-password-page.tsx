import { ArrowRight, Check, Copy, MailCheck, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { ErrorState } from '@/components/feedback/error-state'
import { AuthShell } from '@/components/layout/auth-shell'
import { Alert, AlertDescription, AlertIcon, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Spinner } from '@/components/ui/spinner'
import { requestPasswordResetRequest } from '@/services/auth'
import { toApiError } from '@/services/errors'
import { toast } from '@/stores/toast-store'
import type { ApiError } from '@/lib/api-client'
import type { PasswordResetRequestedResponse } from '@/types'

/**
 * Deliberately loose: it only has to reject what no mailbox could receive before
 * the round trip. Anything stricter starts rejecting addresses that work, and
 * the server is the authority anyway.
 */
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const QUIET_LINK =
  'text-foreground underline-offset-4 transition-colors duration-150 hover:text-primary hover:underline'

/**
 * THE SUCCESS COPY IS NOT CONDITIONAL. Do not "improve" it.
 *
 * `POST /auth/password/forgot` answers 202 with an identical body for a
 * registered address and an unregistered one — that is deliberate (see
 * `AuthService.request_password_reset`): the endpoint must never be usable to
 * discover which addresses hold accounts. Any wording that varies with the
 * response — "we could not find that address", a token panel shown only when an
 * account exists, a different tone or timing — rebuilds the account oracle the
 * backend went out of its way to avoid. The response's `dev_token` is the one
 * value that may be inspected, and only because it is a local-development
 * affordance rather than an account signal.
 */
const NEUTRAL_SUCCESS =
  'If an account exists for that address, a reset link has been sent.'

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  // Client-side errors appear once a submit has been attempted, never before:
  // a form that marks itself wrong while someone is still filling it in reads
  // as a scolding machine rather than as help.
  const [attempted, setAttempted] = useState(false)
  const [pending, setPending] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [issued, setIssued] = useState<PasswordResetRequestedResponse | null>(null)

  const emailRef = useRef<HTMLInputElement>(null)

  const address = email.trim()
  const emailError = !attempted
    ? undefined
    : address.length === 0
      ? 'Enter your email address.'
      : !EMAIL_SHAPE.test(address)
        ? 'Enter a valid email address, for example you@nexus.local.'
        : undefined

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setAttempted(true)

    if (address.length === 0 || !EMAIL_SHAPE.test(address)) {
      emailRef.current?.focus()
      return
    }

    setPending(true)
    setFailure(null)

    try {
      const response = await requestPasswordResetRequest({ email: address })
      setIssued(response)
      toast.success('Reset requested', 'Check your inbox for the link.')
    } catch (cause) {
      setFailure(toApiError(cause))
    } finally {
      setPending(false)
    }
  }

  return (
    <AuthShell
      title="Reset your password"
      description="Give us the address on your account and we will send a link that lets you set a new one."
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
        {/* No `CardHeader` — `AuthShell` already owns the page masthead — so the
            content keeps the card's own side padding instead of `pt-0`. */}
        <CardContent className="pt-6">
          {issued ? (
            <SentPanel
              email={address}
              devToken={issued.dev_token ?? null}
              onUseAnother={() => {
                setIssued(null)
                setAttempted(false)
                emailRef.current?.focus()
              }}
            />
          ) : (
            <form onSubmit={onSubmit} noValidate className="app-form-stack">
              {failure && <ErrorState error={failure} compact />}

              <div className="app-form-field">
                <Label htmlFor="email">Email</Label>
                <Input
                  ref={emailRef}
                  id="email"
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  placeholder="you@nexus.local"
                  value={email}
                  disabled={pending}
                  error={Boolean(emailError)}
                  success={EMAIL_SHAPE.test(address)}
                  aria-describedby={emailError ? 'email-error' : 'email-hint'}
                  onChange={(event) => setEmail(event.target.value)}
                />
                {emailError ? (
                  <p id="email-error" className="app-form-error">
                    {emailError}
                  </p>
                ) : (
                  <p id="email-hint" className="app-form-hint">
                    The address you signed up with. We never use it for anything else.
                  </p>
                )}
              </div>

              <div className="flex flex-col gap-3">
                <Button type="submit" className="w-full" disabled={pending} aria-busy={pending}>
                  {pending && <Spinner size="sm" label="Requesting a reset link" />}
                  {pending ? 'Sending…' : 'Send reset link'}
                </Button>
                <p className="app-form-hint">
                  For your safety the response is identical whether or not the address is
                  registered — nobody can use this form to find out who has an account.
                </p>
              </div>
            </form>
          )}
        </CardContent>
      </Card>
    </AuthShell>
  )
}

interface SentPanelProps {
  email: string
  devToken: string | null
  onUseAnother: () => void
}

/**
 * The post-submit surface. Two states, and the difference between them is a
 * transport detail rather than an account fact — which is exactly why the
 * headline above is the same either way.
 */
function SentPanel({ email, devToken, onUseAnother }: SentPanelProps) {
  const [copied, setCopied] = useState(false)
  const copyTimer = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current)
    },
    [],
  )

  async function copyToken() {
    if (devToken === null) return
    try {
      await navigator.clipboard.writeText(devToken)
      setCopied(true)
      if (copyTimer.current !== null) window.clearTimeout(copyTimer.current)
      copyTimer.current = window.setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error('Could not copy the token', 'Select it above and copy it manually.')
    }
  }

  return (
    <div className="app-form-stack">
      <Alert variant="success">
        <AlertIcon>
          <MailCheck />
        </AlertIcon>
        <div className="min-w-0">
          <AlertTitle>{NEUTRAL_SUCCESS}</AlertTitle>
          <AlertDescription>
            Requested for <span className="text-foreground">{email}</span>. The link expires
            shortly and can only be used once.
          </AlertDescription>
        </div>
      </Alert>

      {devToken !== null ? (
        <div className="app-form-field rounded-lg border border-warning/30 bg-warning/[0.06] p-4">
          <div className="flex flex-wrap items-center gap-2">
            <TriangleAlert className="size-4 shrink-0 text-warning" aria-hidden="true" />
            <p className="text-sm font-medium text-foreground">Reset link shown here</p>
            <Badge variant="warning">Local only</Badge>
          </div>

          <p className="app-form-hint">
            NEXUS runs on your own machine and ships no mail service, so outside production the
            API hands the link back directly rather than emailing it. This value is a credential
            — anyone holding it can take over the account.
          </p>

          <code className="block select-all break-all rounded-md border border-border bg-background px-3 py-2 font-mono text-xs leading-relaxed text-foreground">
            {devToken}
          </code>

          <div className="flex flex-wrap gap-2 pt-0.5">
            <Button asChild>
              <Link to={`/reset-password?token=${encodeURIComponent(devToken)}`}>
                Continue to reset
                <ArrowRight aria-hidden="true" />
              </Link>
            </Button>
            <Button type="button" variant="outline" onClick={copyToken}>
              {copied ? (
                <Check aria-hidden="true" />
              ) : (
                <Copy aria-hidden="true" />
              )}
              {copied ? 'Copied' : 'Copy link token'}
            </Button>
          </div>
        </div>
      ) : (
        <div className="app-form-field rounded-lg border border-border bg-muted/50 p-4">
          <p className="text-sm font-medium text-foreground">No link to show</p>
          <p className="app-form-hint">
            No token came back. The request is acknowledged identically either way, so this
            screen cannot tell you why. In production mode NEXUS never returns the link — it
            goes out through your mail transport, and the message should reach{' '}
            <span className="text-foreground">{email}</span>. Everywhere else there is no mail
            service configured, so the link would have been shown here.
          </p>
        </div>
      )}

      <Button type="button" variant="ghost" size="sm" onClick={onUseAnother} className="self-start">
        Use a different address
      </Button>
    </div>
  )
}
