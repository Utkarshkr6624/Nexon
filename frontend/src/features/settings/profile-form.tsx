import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { Save } from 'lucide-react'

import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Spinner } from '@/components/ui/spinner'
import { ErrorState } from '@/components/feedback/error-state'
import { toApiError } from '@/services/errors'
import type { ApiError } from '@/lib/api-client'
import { selectInitials, useAuthStore } from '@/stores/auth-store'
import { toast } from '@/stores/toast-store'
import type { User } from '@/types'

/**
 * Mirrors `Username` in `backend/app/schemas/user.py`. The same shape check runs
 * in the browser so a typo is caught before a round trip, never instead of one:
 * the server still decides.
 */
const USERNAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$/
const USERNAME_HINT = '3–32 characters, starting with a letter or digit. Letters, digits, _ and - are allowed.'
const MAX_DISPLAY_NAME_LENGTH = 255
const MAX_AVATAR_URL_LENGTH = 2048

/** `field: "username" | "display_name" | "avatar_url"` → the server's message. */
function serverFieldErrors(error: ApiError): Record<string, string> {
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

function validateUsername(value: string): string | null {
  if (value.length === 0) return 'Choose a username.'
  if (!USERNAME_PATTERN.test(value)) return USERNAME_HINT
  return null
}

function validateAvatarUrl(value: string): string | null {
  if (value.length === 0) return null
  if (value.length > MAX_AVATAR_URL_LENGTH) {
    return `Keep the URL under ${MAX_AVATAR_URL_LENGTH} characters.`
  }
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    return 'Enter a full URL, for example https://example.com/avatar.png'
  }
  // The backend accepts http(s) only — anything else, `javascript:` above all,
  // would turn a stored avatar into a script injection.
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return 'Only http:// and https:// image URLs are accepted.'
  }
  return null
}

export function ProfileForm() {
  const user = useAuthStore((state) => state.user)
  const updateProfile = useAuthStore((state) => state.updateProfile)
  const clearError = useAuthStore((state) => state.clearError)

  const [displayName, setDisplayName] = useState(user?.display_name ?? '')
  const [username, setUsername] = useState(user?.username ?? '')
  const [avatarUrl, setAvatarUrl] = useState(user?.avatar_url ?? '')
  const [touched, setTouched] = useState<{ displayName: boolean; username: boolean; avatarUrl: boolean }>({
    displayName: false,
    username: false,
    avatarUrl: false,
  })
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [fieldFailures, setFieldFailures] = useState<Record<string, string>>({})

  const trimmedDisplayName = displayName.trim()
  const trimmedUsername = username.trim()
  const trimmedAvatarUrl = avatarUrl.trim()

  // Errors live next to the field that produced them and are recomputed on
  // every render, so editing a field clears its message without a second piece
  // of state to keep in sync.
  const displayNameError =
    trimmedDisplayName.length > MAX_DISPLAY_NAME_LENGTH
      ? `Keep the display name under ${MAX_DISPLAY_NAME_LENGTH} characters.`
      : null
  const usernameError = validateUsername(trimmedUsername)
  const avatarUrlError = validateAvatarUrl(trimmedAvatarUrl)

  const previewUser: User | null = useMemo(
    () =>
      user
        ? {
            ...user,
            display_name: trimmedDisplayName || null,
            avatar_url: trimmedAvatarUrl || null,
            username: trimmedUsername || user.username,
          }
        : null,
    [user, trimmedDisplayName, trimmedUsername, trimmedAvatarUrl],
  )

  const showAvatar = trimmedAvatarUrl.length > 0 && avatarUrlError === null
  const initials = selectInitials(previewUser)

  const dirty =
    trimmedDisplayName !== (user?.display_name ?? '').trim() ||
    trimmedUsername !== (user?.username ?? '').trim() ||
    trimmedAvatarUrl !== (user?.avatar_url ?? '').trim()

  const valid = usernameError === null && avatarUrlError === null && displayNameError === null
  const canSubmit = dirty && valid && !busy

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit) return

    setBusy(true)
    setFailure(null)
    setFieldFailures({})
    // The store keeps one `error` for every auth action, so it is cleared first:
    // anything present after the call is this submission's own failure.
    clearError()

    try {
      await updateProfile({
        display_name: trimmedDisplayName || null,
        username: trimmedUsername,
        avatar_url: trimmedAvatarUrl || null,
      })
      const error = useAuthStore.getState().error
      if (error) {
        setFailure(error)
        setFieldFailures(serverFieldErrors(error))
        return
      }
      toast.success('Profile updated')
    } catch (cause) {
      const error = toApiError(cause)
      setFailure(error)
      setFieldFailures(serverFieldErrors(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Profile</CardTitle>
        <CardDescription>
          How you appear across NEXUS. Your username is the handle you sign in with; the
          display name is what other people read.
        </CardDescription>
      </CardHeader>

      <CardContent>
        <div className="flex items-center gap-4">
          <Avatar className="size-16">
            {showAvatar && <AvatarImage src={trimmedAvatarUrl} alt="" />}
            <AvatarFallback className="text-base">{initials}</AvatarFallback>
          </Avatar>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-foreground">
              {trimmedDisplayName || 'Unnamed account'}
            </p>
            <p className="truncate text-sm text-muted-foreground">
              {trimmedUsername ? `@${trimmedUsername}` : 'No username yet'}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              Preview — it updates as you type, and is only saved when you save.
            </p>
          </div>
        </div>

        <Separator className="my-6" />

        <form onSubmit={onSubmit} className="app-form-stack" noValidate>
          {failure && <ErrorState error={failure} compact />}

          <div className="app-form-field">
            <Label htmlFor="profile-display-name" optional>
              Display name
            </Label>
            <Input
              id="profile-display-name"
              name="display_name"
              autoComplete="name"
              maxLength={MAX_DISPLAY_NAME_LENGTH}
              placeholder="Ada Lovelace"
              value={displayName}
              error={Boolean(displayNameError || fieldFailures.display_name)}
              onChange={(event) => {
                setDisplayName(event.target.value)
                setTouched((previous) => ({ ...previous, displayName: true }))
                clearError()
                setFailure(null)
              }}
              aria-describedby={
                displayNameError || fieldFailures.display_name ? 'profile-display-name-error' : undefined
              }
            />
            {displayNameError ?? fieldFailures.display_name ? (
              <p id="profile-display-name-error" className="app-form-error">
                {displayNameError ?? fieldFailures.display_name}
              </p>
            ) : null}
          </div>

          <div className="app-form-field">
            <Label htmlFor="profile-username">Username</Label>
            <Input
              id="profile-username"
              name="username"
              autoComplete="username"
              maxLength={32}
              spellCheck={false}
              autoCapitalize="none"
              placeholder="ada"
              value={username}
              error={Boolean(usernameError || fieldFailures.username)}
              success={
                touched.username && !usernameError && !fieldFailures.username && username !== ''
              }
              onChange={(event) => {
                setUsername(event.target.value)
                setTouched((previous) => ({ ...previous, username: true }))
                clearError()
                setFailure(null)
              }}
              aria-describedby={usernameError || fieldFailures.username ? 'profile-username-error' : 'profile-username-hint'}
            />
            {usernameError || fieldFailures.username ? (
              <p id="profile-username-error" className="app-form-error">
                {usernameError ?? fieldFailures.username}
              </p>
            ) : (
              <p id="profile-username-hint" className="app-form-hint">
                {USERNAME_HINT}
              </p>
            )}
          </div>

          <div className="app-form-field">
            <Label htmlFor="profile-avatar-url" optional>
              Avatar URL
            </Label>
            <Input
              id="profile-avatar-url"
              name="avatar_url"
              type="url"
              inputMode="url"
              spellCheck={false}
              autoCapitalize="none"
              placeholder="https://example.com/avatar.png"
              value={avatarUrl}
              error={Boolean(avatarUrlError || fieldFailures.avatar_url)}
              success={
                touched.avatarUrl && !avatarUrlError && !fieldFailures.avatar_url && avatarUrl !== ''
              }
              onChange={(event) => {
                setAvatarUrl(event.target.value)
                setTouched((previous) => ({ ...previous, avatarUrl: true }))
                clearError()
                setFailure(null)
              }}
              aria-describedby={
                avatarUrlError || fieldFailures.avatar_url ? 'profile-avatar-url-error' : 'profile-avatar-url-hint'
              }
            />
            {avatarUrlError || fieldFailures.avatar_url ? (
              <p id="profile-avatar-url-error" className="app-form-error">
                {avatarUrlError ?? fieldFailures.avatar_url}
              </p>
            ) : (
              <p id="profile-avatar-url-hint" className="app-form-hint">
                Link to an image NEXUS can load. Leave it empty to fall back to your initials.
              </p>
            )}
          </div>

          <div className="space-y-2 rounded-md border border-dashed border-border bg-muted/40 p-3">
            <p className="text-xs font-medium uppercase tracking-[0.1em] text-muted-foreground">
              Account identity
            </p>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="truncate font-mono text-sm text-foreground">
                {user?.email ?? 'Not signed in'}
              </span>
              <Badge variant={user?.is_verified ? 'success' : 'warning'}>
                {user?.is_verified ? 'Verified' : 'Not verified'}
              </Badge>
            </div>
            <p className="app-form-hint">
              Your email address is the account identity, so it is shown here rather than
              edited. Changing it needs a verified flow that NEXUS does not have yet.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <Button type="submit" disabled={!canSubmit}>
              {busy && <Spinner size="sm" />}
              <Save aria-hidden="true" />
              {busy ? 'Saving…' : 'Save changes'}
            </Button>
            <p className="text-xs text-muted-foreground">
              {dirty ? 'Unsaved changes' : 'Everything is saved'}
            </p>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}
