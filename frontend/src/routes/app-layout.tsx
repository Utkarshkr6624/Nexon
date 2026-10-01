import { Outlet } from 'react-router-dom'

import { AppShell } from '@/components/layout/app-shell'
import { RequireAuth } from '@/routes/guards'

/**
 * Layout route for every authenticated destination: the session gate, the
 * application shell and the lazy-route boundary all live here.
 */
export function AppLayout() {
  return (
    <RequireAuth>
      <AppShell>
        <Outlet />
      </AppShell>
    </RequireAuth>
  )
}