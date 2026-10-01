import { Suspense } from 'react'
import type { ReactNode } from 'react'

import { Brand } from '@/components/brand/logo'
import { Spinner } from '@/components/ui/spinner'

export interface AuthRouteBoundaryProps {
  children: ReactNode
}

function AuthFallback() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background">
      <Brand />
      <Spinner label="Loading" />
    </div>
  )
}

/**
 * Suspense boundary for the lazy auth pages. The application's own boundary
 * lives inside the shell, so without this /login commits nothing for one commit
 * while its chunk resolves and flashes blank on a direct load or hard refresh.
 */
export function AuthRouteBoundary({ children }: AuthRouteBoundaryProps) {
  return <Suspense fallback={<AuthFallback />}>{children}</Suspense>
}
