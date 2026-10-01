import type { ReactNode } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'

import { AuthBootstrap } from '@/app/auth-bootstrap'
import { queryClient } from '@/app/query-client'
import { ThemeProvider } from '@/app/theme-provider'
import { Toaster } from '@/components/ui/toaster'
import { TooltipProvider } from '@/components/ui/tooltip'

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <TooltipProvider delayDuration={200}>
          <AuthBootstrap>{children}</AuthBootstrap>
        </TooltipProvider>
        {/*
         * A sibling of the routed tree, not a child of it: notifications have to
         * survive a route crashing into its error boundary, and an auth failure
         * must be reportable too. It sits inside ThemeProvider so a toast
         * inherits the same palette as the page it reports on.
         */}
        <Toaster />
      </ThemeProvider>
    </QueryClientProvider>
  )
}