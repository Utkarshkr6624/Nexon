import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

/**
 * The WAI-ARIA tabs pattern, hand-rolled because no tabs package is installed.
 *
 * The parts that matter to a keyboard user are roving tabindex (exactly one tab
 * stop for the whole list, so Tab moves into the panel rather than through
 * every tab) and arrow-key selection (focus and selection move together). Both
 * are asserted here, along with the relationship wiring between triggers and
 * panels.
 */

function renderTabs(onValueChange?: (value: string) => void) {
  return render(
    <Tabs defaultValue="profile" onValueChange={onValueChange}>
      <TabsList>
        <TabsTrigger value="profile">Profile</TabsTrigger>
        <TabsTrigger value="appearance">Appearance</TabsTrigger>
        <TabsTrigger value="sessions">Sessions</TabsTrigger>
      </TabsList>
      <TabsContent value="profile">Profile panel</TabsContent>
      <TabsContent value="appearance">Appearance panel</TabsContent>
      <TabsContent value="sessions">Sessions panel</TabsContent>
    </Tabs>,
  )
}

function trigger(name: string): HTMLElement {
  return screen.getByRole('tab', { name })
}

describe('Tabs', () => {
  it('wires the tablist, tabs and panels together by id', () => {
    renderTabs()

    const list = screen.getByRole('tablist')
    expect(list).toHaveAttribute('aria-orientation', 'horizontal')

    const tabs = screen.getAllByRole('tab')
    expect(tabs).toHaveLength(3)

    const panels = screen.getAllByRole('tabpanel', { hidden: true })
    expect(panels).toHaveLength(3)

    for (const tab of tabs) {
      const panel = document.getElementById(tab.getAttribute('aria-controls') as string)
      expect(panel).not.toBeNull()
      expect(panel?.getAttribute('aria-labelledby')).toBe(tab.id)
    }
  })

  it('marks exactly one tab as selected and shows only its panel', () => {
    renderTabs()

    expect(trigger('Profile')).toHaveAttribute('aria-selected', 'true')
    expect(trigger('Appearance')).toHaveAttribute('aria-selected', 'false')
    expect(trigger('Sessions')).toHaveAttribute('aria-selected', 'false')

    expect(screen.getByRole('tabpanel')).toHaveTextContent('Profile panel')
    // The other panels stay mounted but leave the layout and the accessibility
    // tree, so a half-filled form survives a tab switch.
    expect(
      screen.queryByRole('tabpanel', { name: 'Appearance' }),
    ).not.toBeInTheDocument()
    expect(document.body.textContent).toContain('Appearance panel')
  })

  it('uses roving tabindex, so the list is a single tab stop', () => {
    renderTabs()

    expect(trigger('Profile')).toHaveAttribute('tabindex', '0')
    expect(trigger('Appearance')).toHaveAttribute('tabindex', '-1')
    expect(trigger('Sessions')).toHaveAttribute('tabindex', '-1')

    expect(screen.getAllByRole('tab').filter((tab) => tab.getAttribute('tabindex') === '0'))
      .toHaveLength(1)
  })

  it('moves the roving tab stop when the selection changes', async () => {
    const user = userEvent.setup()
    renderTabs()

    await user.click(trigger('Sessions'))

    expect(trigger('Sessions')).toHaveAttribute('tabindex', '0')
    expect(trigger('Profile')).toHaveAttribute('tabindex', '-1')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Sessions panel')
  })

  it('selects with the arrow keys and wraps at both ends', async () => {
    const user = userEvent.setup()
    renderTabs()

    trigger('Profile').focus()

    await user.keyboard('{ArrowRight}')
    expect(trigger('Appearance')).toHaveAttribute('aria-selected', 'true')
    expect(trigger('Appearance')).toHaveFocus()
    expect(trigger('Appearance')).toHaveAttribute('tabindex', '0')

    await user.keyboard('{ArrowRight}')
    expect(trigger('Sessions')).toHaveAttribute('aria-selected', 'true')
    expect(trigger('Sessions')).toHaveFocus()

    // Past the end comes back to the start rather than dead-ending.
    await user.keyboard('{ArrowRight}')
    expect(trigger('Profile')).toHaveAttribute('aria-selected', 'true')
    expect(trigger('Profile')).toHaveFocus()

    await user.keyboard('{ArrowLeft}')
    expect(trigger('Sessions')).toHaveAttribute('aria-selected', 'true')
    expect(trigger('Sessions')).toHaveFocus()
  })

  it('jumps to the ends with Home and End', async () => {
    const user = userEvent.setup()
    renderTabs()

    trigger('Appearance').focus()

    await user.keyboard('{Home}')
    expect(trigger('Profile')).toHaveAttribute('aria-selected', 'true')
    expect(trigger('Profile')).toHaveFocus()

    await user.keyboard('{End}')
    expect(trigger('Sessions')).toHaveAttribute('aria-selected', 'true')
    expect(trigger('Sessions')).toHaveFocus()
  })

  it('reports the new value to a controlled caller', async () => {
    const onValueChange = vi.fn()
    const user = userEvent.setup()
    renderTabs(onValueChange)

    await user.click(trigger('Appearance'))
    expect(onValueChange).toHaveBeenCalledWith('appearance')

    // Re-selecting the current tab is not a change.
    await user.click(trigger('Appearance'))
    expect(onValueChange).toHaveBeenCalledTimes(1)
  })

  it('does not move selection on a key it does not handle', async () => {
    const onValueChange = vi.fn()
    const user = userEvent.setup()
    renderTabs(onValueChange)

    trigger('Profile').focus()
    await user.keyboard('{ArrowDown}')

    expect(trigger('Profile')).toHaveAttribute('aria-selected', 'true')
    expect(onValueChange).not.toHaveBeenCalled()
  })
})