import type { ReactNode } from 'react'

import { Brand } from '@/components/brand/logo'

export interface AuthShellProps {
  title: string
  description: string
  children: ReactNode
  footer: ReactNode
}

/**
 * Chrome for /login and /register. Deliberately spare: one card, no
 * navigation, so authentication reads as a separate surface from the app.
 */
export function AuthShell({ title, description, children, footer }: AuthShellProps) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="flex justify-center">
          <Brand />
        </div>

        <div className="mt-8 space-y-6">
          <div className="space-y-1.5 text-center">
            <h1 className="text-lg font-semibold tracking-tight text-foreground">{title}</h1>
            <p className="text-sm leading-relaxed text-muted-foreground">{description}</p>
          </div>

          {children}
        </div>

        <div className="mt-6 text-center text-sm text-muted-foreground">{footer}</div>
      </div>
    </div>
  )
}