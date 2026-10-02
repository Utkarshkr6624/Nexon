import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Trash2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Spinner } from '@/components/ui/spinner'
import { ErrorState } from '@/components/feedback/error-state'
import { toApiError } from '@/services/errors'
import type { ApiError } from '@/lib/api-client'
import { useAuthStore } from '@/stores/auth-store'
import { toast } from '@/stores/toast-store'

const ACKNOWLEDGEMENT =
  'I understand my account and everything in it will be permanently deleted.'

/**
 * Account deletion. The backend requires `{ password, confirm: true }` — a
 * bearer token alone is not enough authority for an irreversible action — so
 * this asks for both, and says plainly that there is no way back rather than
 * burying it in a confirmation.
 */
export function DangerZone() {
  const navigate = useNavigate()
  const deleteAccount = useAuthStore((state) => state.deleteAccount)
  const logout = useAuthStore((state) => state.logout)
  const clearError = useAuthStore((state) => state.clearError)

  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const ready = password.length > 0 && acknowledged && !busy

  function onOpenChange(next: boolean) {
    if (busy) return
    setOpen(next)
    if (!next) {
      setPassword('')
      setAcknowledged(false)
      setFailure(null)
      clearError()
    }
  }

  async function onSubmit() {
    if (!ready) return

    setBusy(true)
    setFailure(null)
    clearError()

    try {
      await deleteAccount({ password, confirm: true })
      const error = useAuthStore.getState().error
      if (error) {
        setFailure(error)
        return
      }

      setOpen(false)
      toast.info('Account deleted', 'This account and its data are gone.')
      // The store normally clears the session as part of the call; if it did
      // not, the token is dead anyway and the local session must go with it.
      if (useAuthStore.getState().status === 'authenticated') {
        await logout()
      }
      navigate('/login', { replace: true })
    } catch (cause) {
      setFailure(toApiError(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="border-t border-border pt-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
        <div className="min-w-0 space-y-1">
          <h3 className="text-sm font-semibold text-muted-foreground">Danger zone</h3>
          <p className="max-w-prose text-sm leading-relaxed text-muted-foreground">
            Deleting your account removes your profile and every record that belongs to it, and
            signs out all of your devices. It cannot be undone — there is no backup and no
            restore. If you only want to stop being signed in somewhere, use the sessions list
            instead.
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          className="shrink-0 border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
          onClick={() => setOpen(true)}
        >
          <Trash2 aria-hidden="true" />
          Delete account
        </Button>
      </div>

      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete your account?</DialogTitle>
            <DialogDescription>
              This is permanent. Your profile, sessions and every record linked to this account
              are deleted, and cannot be recovered afterwards.
            </DialogDescription>
          </DialogHeader>

          <form
            onSubmit={(event) => {
              event.preventDefault()
              void onSubmit()
            }}
            className="app-form-stack"
            noValidate
          >
            {failure && <ErrorState error={failure} compact />}

            <div className="app-form-field">
              <Label htmlFor="danger-password">Confirm with your password</Label>
              <Input
                id="danger-password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => {
                  setPassword(event.target.value)
                  setFailure(null)
                }}
                aria-describedby="danger-password-hint"
              />
              <p id="danger-password-hint" className="app-form-hint">
                Enter the password for this account, not a reset link.
              </p>
            </div>

            <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border p-3 transition-colors hover:bg-accent">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(event) => {
                  setAcknowledged(event.target.checked)
                  setFailure(null)
                }}
                className="mt-0.5 size-4 shrink-0 rounded border-input accent-primary"
              />
              <span className="text-sm leading-relaxed text-foreground">{ACKNOWLEDGEMENT}</span>
            </label>
          </form>

          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline" disabled={busy}>
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="button"
              variant="destructive"
              disabled={!ready}
              onClick={() => void onSubmit()}
            >
              {busy && <Spinner size="sm" />}
              {busy ? 'Deleting…' : 'Delete my account'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  )
}
