import { useNavigate } from 'react-router-dom'
import { LogOut, Settings, ShieldCheck, User } from 'lucide-react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Spinner } from '@/components/ui/spinner'
import { selectDisplayName, selectInitials, useAuthStore } from '@/stores/auth-store'

export function UserMenu() {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const logout = useAuthStore((state) => state.logout)
  const pending = useAuthStore((state) => state.pending)

  const displayName = selectDisplayName(user)
  const initials = selectInitials(user)
  const email = user?.email ?? 'Not signed in'

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Account menu for ${displayName}`}
          className="flex items-center gap-2 rounded-md px-1.5 py-1 transition-colors duration-150 ease-out hover:bg-accent"
        >
          <Avatar className="size-7">
            <AvatarFallback>{initials}</AvatarFallback>
          </Avatar>
          <span className="hidden min-w-0 text-left leading-tight md:block">
            <span className="block truncate text-sm font-medium text-foreground">{displayName}</span>
            <span className="block truncate text-xs text-muted-foreground">{email}</span>
          </span>
        </button>
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="flex flex-col items-start gap-0.5 py-2">
          <span className="text-sm font-medium text-foreground">{displayName}</span>
          <span className="text-xs font-normal text-muted-foreground">{email}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled onSelect={(event) => event.preventDefault()}>
          <User aria-hidden="true" />
          Profile
          <span className="ml-auto text-xs text-muted-foreground">Phase 8</span>
        </DropdownMenuItem>
        <DropdownMenuItem disabled onSelect={(event) => event.preventDefault()}>
          <ShieldCheck aria-hidden="true" />
          Security
          <span className="ml-auto text-xs text-muted-foreground">Phase 8</span>
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => navigate('/settings')}>
          <Settings aria-hidden="true" />
          Preferences
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          variant="destructive"
          disabled={pending}
          onSelect={() => {
            // Navigate only once the local session is gone: navigating while
            // `status` is still `authenticated` bounces off the anonymous guard
            // and drops the user back on the dashboard.
            void logout().then(() => navigate('/login', { replace: true }))
          }}
        >
          {pending ? <Spinner size="sm" /> : <LogOut aria-hidden="true" />}
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}