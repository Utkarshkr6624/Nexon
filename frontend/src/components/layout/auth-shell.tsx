import type { ReactNode } from 'react'
import { Laptop } from 'lucide-react'

import { Brand } from '@/components/brand/logo'

export interface AuthShellProps {
  title: string
  description: string
  children: ReactNode
  footer: ReactNode
}

/**
 * Chrome for every signed-out screen: sign in, register, and password
 * recovery.
 *
 * Deliberately spare — brand, masthead, one surface, footer — so
 * authentication reads as a separate place from the app rather than as a
 * half-populated dashboard. The panel is the caller's `Card`: this shell never
 * wraps `children` in a second surface, because two nested borders and two
 * shadows is what makes a form look assembled rather than designed.
 *
 * The measure is capped at `max-w-md` (28rem), which holds a line of 14px text
 * to roughly 65 characters — past that, the eye loses the return sweep on a
 * short sentence, and these screens are all one column of prose.
 *
 * The vertical rhythm is set for a 900px laptop: a sign-in screen that has to
 * be scrolled to reach the footer is a screen with a hidden bottom, and the
 * register form is already the tallest thing here. Gaps are tight, the masthead
 * is one line and a half, and nothing is dropped to buy the height — the
 * local-first note and the cross-link stay on screen.
 */
export function AuthShell({ title, description, children, footer }: AuthShellProps) {
  return (
    <div className="app-auth-backdrop">
      <main className="flex w-full max-w-md flex-col gap-6">
        <div className="flex justify-center">
          <Brand />
        </div>

        <div className="space-y-5">
          {/* `text-2xl` rather than `text-xl`: this is the page's only heading
              and its only piece of display type, and at 20px it read as a card
              title rather than as the name of the screen. One step, not a
              banner — the description below it still does the explaining. */}
          <header className="space-y-1.5 text-center">
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
            <p className="text-sm leading-relaxed text-muted-foreground">{description}</p>
          </header>

          {children}
        </div>

        <div className="space-y-3 text-center">
          <p className="text-sm text-muted-foreground">{footer}</p>

          {/* The reason this product exists, stated once, quietly. Nothing above
              sells it, and the signed-out screen is the only place a new user is
              guaranteed to read it. */}
          <p className="flex items-start justify-center gap-2 border-t border-border pt-3 text-xs leading-relaxed text-muted-foreground">
            <Laptop className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            <span>
              NEXUS is local-first. It runs on this machine, against a database you own, and
              nothing you put in it is sent anywhere else.
            </span>
          </p>
        </div>
      </main>
    </div>
  )
}
