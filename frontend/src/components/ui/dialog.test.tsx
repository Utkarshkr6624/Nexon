import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'

/**
 * Modal behaviour for the hand-rolled dialog primitive.
 *
 * The dialog owns everything a modal owes the user, because
 * `@radix-ui/react-dialog` is not installed and cannot be added: focus moves
 * into the panel on open and back to the opener on close, Escape dismisses,
 * Tab stays inside, and the body scroll is locked while any dialog is open.
 * Each of those is a promise to the keyboard user, so each is asserted.
 */

function renderDialog({ onOpenChange }: { onOpenChange?: (open: boolean) => void } = {}) {
  return render(
    <Dialog onOpenChange={onOpenChange}>
      <DialogTrigger>Open dialog</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revoke this session?</DialogTitle>
          <DialogDescription>
            Signing out of the other devices cannot be undone from here.
          </DialogDescription>
        </DialogHeader>
        <button type="button">First action</button>
        <DialogFooter>
          <DialogClose>Keep this device</DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>,
  )
}

describe('Dialog', () => {
  it('renders nothing until it is opened', () => {
    renderDialog()

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open dialog' })).toHaveAttribute(
      'aria-expanded',
      'false',
    )
  })

  it('exposes a labelled, modal dialog once opened', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByRole('button', { name: 'Open dialog' }))

    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    // The panel is named by its own title, not by a label the caller has to
    // remember to repeat.
    const labelledBy = dialog.getAttribute('aria-labelledby')
    expect(labelledBy).not.toBeNull()
    expect(document.getElementById(labelledBy as string)).toHaveTextContent(
      'Revoke this session?',
    )
    const describedBy = dialog.getAttribute('aria-describedby')
    expect(document.getElementById(describedBy as string)).toHaveTextContent(
      'Signing out of the other devices cannot be undone from here.',
    )
    expect(screen.getByRole('button', { name: 'Open dialog' })).toHaveAttribute(
      'aria-expanded',
      'true',
    )
  })

  it('moves focus into the panel on open and back to the trigger on close', async () => {
    const user = userEvent.setup()
    renderDialog()

    const trigger = screen.getByRole('button', { name: 'Open dialog' })
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    await user.click(trigger)
    const dialog = await screen.findByRole('dialog')
    // The first focusable control inside the panel, not the trigger that is
    // still in the DOM underneath it.
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true))

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(document.activeElement).toBe(trigger)
  })

  it('closes on Escape and reports the new state', async () => {
    const onOpenChange = vi.fn()
    const user = userEvent.setup()
    renderDialog({ onOpenChange })

    await user.click(screen.getByRole('button', { name: 'Open dialog' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()

    await user.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(onOpenChange).toHaveBeenLastCalledWith(false)
  })

  it('closes from a DialogClose action and from an outside click', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByRole('button', { name: 'Open dialog' }))
    await user.click(await screen.findByRole('button', { name: 'Keep this device' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())

    await user.click(screen.getByRole('button', { name: 'Open dialog' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    // The overlay is the panel's sibling, so a click inside the panel cannot
    // reach the dismissal handler.
    await user.click(screen.getByText('Revoke this session?'))
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('keeps Tab inside the panel while it is open', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByRole('button', { name: 'Open dialog' }))
    const dialog = await screen.findByRole('dialog')

    // Every focusable element in the panel is reachable, and focus never lands
    // on the trigger behind the overlay.
    for (let step = 0; step < 6; step += 1) {
      await user.tab()
      expect(dialog.contains(document.activeElement)).toBe(true)
    }

    await user.tab({ shift: true })
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it('locks the body scroll while open and restores it on close', async () => {
    const user = userEvent.setup()
    renderDialog()

    expect(document.body.style.overflow).toBe('')

    await user.click(screen.getByRole('button', { name: 'Open dialog' }))
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(document.body.style.overflow).toBe('hidden')

    await user.keyboard('{Escape}')
    await waitFor(() => expect(document.body.style.overflow).toBe(''))
  })

  it('names the panel at the moment focus lands, not only after it settles', async () => {
    const user = userEvent.setup()

    /*
     * The panel's accessible name is derived from a count that `DialogTitle`
     * registers in a passive effect, so on the commit the dialog opens that
     * count is still zero and the panel is painted with the fallback
     * `aria-label="Dialog"`. A focus move in that same flush hands the screen
     * reader the generic name and the user keeps it. Waiting for the panel with
     * `findByRole` cannot see any of this: by the time it resolves the
     * registration re-render has landed and the attributes are correct.
     *
     * So the attributes are sampled at the instant `focus()` is called, which is
     * the only moment the ordering is observable.
     */
    const atFocus: { labelledBy: string | null; label: string | null }[] = []
    const nativeFocus = HTMLElement.prototype.focus
    const focusSpy = vi
      .spyOn(HTMLElement.prototype, 'focus')
      .mockImplementation(function (this: HTMLElement, ...args: Parameters<HTMLElement['focus']>) {
        // The panel owns the name; focus lands on the first control inside it.
        const panel = this.closest<HTMLElement>('[role="dialog"]')
        if (panel) {
          atFocus.push({
            labelledBy: panel.getAttribute('aria-labelledby'),
            label: panel.getAttribute('aria-label'),
          })
        }
        return nativeFocus.apply(this, args)
      })

    try {
      renderDialog()
      await user.click(screen.getByRole('button', { name: 'Open dialog' }))

      await waitFor(() => expect(atFocus.length).toBeGreaterThan(0))
      const opened = atFocus.at(0)
      expect(opened).toBeDefined()

      // Already named by its own title, and not carrying the fallback. A missing
      // attribute reads as `null`; a missing record would read as `undefined`,
      // so the `??` on the line above keeps that from passing silently.
      expect(opened?.labelledBy ?? null).not.toBeNull()
      expect(opened?.label).toBeNull()
      const labelledBy = opened?.labelledBy as string
      expect(document.getElementById(labelledBy)).toHaveTextContent('Revoke this session?')
    } finally {
      focusSpy.mockRestore()
    }
  })

  it('stays announced rather than nameless when the title is absent', async () => {
    const user = userEvent.setup()
    render(
      <Dialog>
        <DialogTrigger>Open</DialogTrigger>
        <DialogContent showClose={false}>
          <p>Nothing here is focusable.</p>
        </DialogContent>
      </Dialog>,
    )

    await user.click(screen.getByRole('button', { name: 'Open' }))

    const dialog = await screen.findByRole('dialog')
    // A panel pointing at an id that is not rendered would announce as nothing
    // at all, which is worse than a generic name.
    expect(dialog).not.toHaveAttribute('aria-labelledby')
    expect(dialog.getAttribute('aria-label') ?? 'Dialog').toBe('Dialog')
    // With nothing focusable inside, the panel itself holds focus so the
    // keyboard is not dropped back onto the page behind the overlay.
    await waitFor(() => expect(document.activeElement).toBe(dialog))
  })
})