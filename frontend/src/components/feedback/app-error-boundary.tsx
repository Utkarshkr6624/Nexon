import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { useRouteError } from 'react-router-dom'
import { RefreshCw } from 'lucide-react'

import { ErrorState } from '@/components/feedback/error-state'
import { Button } from '@/components/ui/button'
import { ApiError } from '@/lib/api-client'

const CRASH_TITLE = 'NEXUS could not finish loading this view'

function reload(): void {
  window.location.reload()
}

/**
 * `ErrorState` describes failed requests; a render crash has no request behind
 * it, so the failure is normalised into the generic 500 surface and the real
 * cause travels as the error message.
 */
function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error

  return new ApiError({
    status: 500,
    code: 'internal_error',
    message: error instanceof Error ? error.message : 'Unexpected application error',
  })
}

interface ErrorScreenProps {
  error: unknown
}

function ErrorScreen({ error }: ErrorScreenProps) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-md space-y-4">
        <ErrorState error={toApiError(error)} title={CRASH_TITLE} />
        <div className="flex justify-center">
          <Button type="button" variant="outline" onClick={reload}>
            <RefreshCw aria-hidden="true" />
            Reload
          </Button>
        </div>
      </div>
    </div>
  )
}

export interface AppErrorBoundaryProps {
  children: ReactNode
}

interface AppErrorBoundaryState {
  error: Error | null
}

/**
 * Last line of defence around the whole application. The usual trigger is a
 * lazy chunk that 404s because `dist/` is older than the tab that is asking for
 * it; reloading is the recovery in that case, and it is the only one that works
 * without a new navigation.
 */
export class AppErrorBoundary extends Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  override state: AppErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { error }
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('NEXUS render error', error, info.componentStack)
  }

  override render() {
    if (!this.state.error) return this.props.children
    return <ErrorScreen error={this.state.error} />
  }
}

/**
 * Route-scoped counterpart of `AppErrorBoundary`, so a throw inside one branch
 * of the router degrades that branch instead of the whole app.
 */
export function RouteErrorBoundary() {
  return <ErrorScreen error={useRouteError()} />
}
