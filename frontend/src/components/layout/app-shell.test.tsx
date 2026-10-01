import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import { AppShell } from '@/components/layout/app-shell'
import { TooltipProvider } from '@/components/ui/tooltip'

/**
 * The shell's keyboard contract: a way past the navigation, and an off-canvas
 * sheet that behaves modally while it is open and disappears from the tab order
 * once it is not.
 */

const originalMatchMedia = window.matchMedia

afterEach(() => {
  window.matchMedia = originalMatchMedia
})

function useViewport(desktop: boolean) {
  window.matchMedia = ((query: string) => ({
    media: query,
    matches: desktop ? query.includes('min-width: 1024px') : false,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

function renderShell() {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <AppShell>
          <p>Page content</p>
        </AppShell>
      </TooltipProvider>
    </MemoryRouter>,
  )
}

function sidebar(): HTMLElement {
  return screen.getByRole('navigation', { name: 'Primary' }).closest('div.fixed') as HTMLElement
}

describe('AppShell accessibility', () => {
  it('offers a skip link that lands on the focusable main region', () => {
    renderShell()

    const skip = screen.getByRole('link', { name: 'Skip to content' })
    const main = screen.getByRole('main')

    expect(skip).toHaveAttribute('href', `#${main.id}`)
    expect(main.id).not.toBe('')
    expect(main).toHaveAttribute('tabindex', '-1')
  })

  it('takes the closed sheet out of the tab order below lg', () => {
    useViewport(false)
    renderShell()

    expect(sidebar()).toHaveAttribute('inert')
  })

  it('keeps the persistent sidebar interactive at lg', () => {
    useViewport(true)
    renderShell()

    expect(sidebar()).not.toHaveAttribute('inert')
  })

  it('moves focus into the open sheet, holds it there, and returns it on close', async () => {
    useViewport(false)
    const user = userEvent.setup()
    renderShell()

    const trigger = screen.getByRole('button', { name: 'Open navigation' })
    await user.click(trigger)

    const nav = screen.getByRole('navigation', { name: 'Primary' })
    expect(sidebar()).not.toHaveAttribute('inert')
    expect(screen.getByRole('main').closest('div.flex')).toHaveAttribute('inert')
    await waitFor(() => expect(nav.contains(document.activeElement)).toBe(true))

    // Tab from the last destination stays inside the sheet.
    const links = within(sidebar()).getAllByRole('link')
    const firstLink = links[0]
    const lastLink = links[links.length - 1]
    expect(links.length).toBeGreaterThan(1)
    lastLink?.focus()
    await user.tab()
    expect(sidebar().contains(document.activeElement)).toBe(true)

    // Shift+Tab from the first one does too.
    firstLink?.focus()
    await user.tab({ shift: true })
    expect(sidebar().contains(document.activeElement)).toBe(true)

    await user.keyboard('{Escape}')
    await waitFor(() => expect(document.activeElement).toBe(trigger))
  })

  it('restores the collapse preference instead of resetting it on mount', async () => {
    useViewport(true)
    window.localStorage.setItem('nexus.sidebar.collapsed', 'true')
    const user = userEvent.setup()

    renderShell()
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Expand sidebar' }))
    expect(window.localStorage.getItem('nexus.sidebar.collapsed')).toBe('false')
  })
})
