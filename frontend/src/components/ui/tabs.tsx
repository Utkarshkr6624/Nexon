import * as React from 'react'

import { cn } from '@/lib/utils'

interface TabsContextValue {
  /** Value of the selected tab; `''` when nothing is selected yet. */
  value: string
  select: (value: string) => void
  triggerId: (value: string) => string
  panelId: (value: string) => string
}

const TabsContext = React.createContext<TabsContextValue | null>(null)

function useTabsContext(component: string): TabsContextValue {
  const context = React.useContext(TabsContext)
  if (!context) {
    throw new Error(`\`${component}\` must be rendered inside a \`<Tabs>\`.`)
  }
  return context
}

/**
 * Roving focus for the tablist, per the WAI-ARIA tabs pattern: the arrow keys
 * move focus *and* selection together, Home/End jump to the ends, and focus
 * wraps. Order comes from the DOM, so the tablist's children stay the single
 * source of truth for tab order.
 */
function handleRovingKeys(
  event: React.KeyboardEvent<HTMLButtonElement>,
  select: (value: string) => void,
): void {
  const isNext = event.key === 'ArrowRight'
  const isPrevious = event.key === 'ArrowLeft'
  if (!isNext && !isPrevious && event.key !== 'Home' && event.key !== 'End') return

  const list = event.currentTarget.closest('[role="tablist"]')
  if (!list) return

  const tabs = Array.from(
    list.querySelectorAll<HTMLButtonElement>('[role="tab"]:not([disabled])'),
  ).filter((tab) => tab.getAttribute('aria-disabled') !== 'true')
  const count = tabs.length
  if (count === 0) return

  const current = tabs.indexOf(event.currentTarget)
  let targetIndex: number
  if (event.key === 'Home') {
    targetIndex = 0
  } else if (event.key === 'End') {
    targetIndex = count - 1
  } else if (isPrevious) {
    targetIndex = (current - 1 + count) % count
  } else {
    targetIndex = (current + 1) % count
  }

  const target = tabs[targetIndex]
  const targetValue = target?.dataset.value
  if (!target || targetValue === undefined) return

  // Arrow keys must not also scroll the settings page underneath the tablist.
  event.preventDefault()
  target.focus()
  select(targetValue)
}

export interface TabsProps extends React.ComponentPropsWithoutRef<'div'> {
  /**
   * Initially selected tab for the uncontrolled case. Supply either this or
   * `value`: with neither, no trigger is selected and the tablist exposes no
   * tab stop, so it is unreachable by keyboard.
   */
  defaultValue?: string
  /** Selected tab for the controlled case. */
  value?: string
  onValueChange?: (value: string) => void
}

/**
 * Horizontal tabs, hand-rolled: no Radix tabs package is installed, and the
 * part that matters — roving tabindex, arrow/Home/End selection, and keeping
 * inactive panels mounted — is small enough to own.
 *
 * Only the horizontal orientation is supported, so the vertical arrow keys are
 * deliberately not handled and `aria-orientation` stays horizontal.
 */
function Tabs({
  className,
  defaultValue,
  value: controlledValue,
  onValueChange,
  ...props
}: TabsProps) {
  const [uncontrolledValue, setUncontrolledValue] = React.useState(defaultValue ?? '')
  const isControlled = controlledValue !== undefined
  const value = isControlled ? controlledValue : uncontrolledValue

  const select = React.useCallback(
    (next: string) => {
      if (next === value) return
      if (!isControlled) setUncontrolledValue(next)
      onValueChange?.(next)
    },
    [isControlled, onValueChange, value],
  )

  // `useId` is unique but may contain colons, which are legal in an `id`
  // attribute yet break any CSS selector built from it (dev tools, test queries).
  const baseId = React.useId().replace(/:/g, '')

  const context = React.useMemo<TabsContextValue>(
    () => ({
      value,
      select,
      triggerId: (tabValue: string) => `${baseId}-tab-${tabValue}`,
      panelId: (tabValue: string) => `${baseId}-panel-${tabValue}`,
    }),
    [baseId, select, value],
  )

  return (
    <TabsContext.Provider value={context}>
      <div className={cn('w-full', className)} {...props} />
    </TabsContext.Provider>
  )
}
Tabs.displayName = 'Tabs'

function TabsList({ className, ...props }: React.ComponentPropsWithoutRef<'div'>) {
  return (
    <div
      role="tablist"
      aria-orientation="horizontal"
      className={cn(
        'inline-flex items-stretch gap-1 border-b border-border text-muted-foreground',
        className,
      )}
      {...props}
    />
  )
}
TabsList.displayName = 'TabsList'

export interface TabsTriggerProps
  extends React.ComponentPropsWithoutRef<'button'> {
  value: string
}

function TabsTrigger({ className, value, onClick, onKeyDown, ...props }: TabsTriggerProps) {
  const { value: selected, select, triggerId, panelId } = useTabsContext('TabsTrigger')
  const isSelected = selected === value

  return (
    <button
      type="button"
      role="tab"
      id={triggerId(value)}
      aria-selected={isSelected}
      aria-controls={panelId(value)}
      data-state={isSelected ? 'active' : 'inactive'}
      data-value={value}
      // Roving tabindex: exactly one trigger is in the tab order, so Tab moves
      // out of the tablist into the panel rather than through every tab.
      tabIndex={isSelected ? 0 : -1}
      className={cn(
        'inline-flex items-center justify-center whitespace-nowrap border-b-2 border-transparent px-3 pb-2 pt-2 text-sm font-medium text-muted-foreground transition-colors',
        'hover:text-foreground',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        'disabled:pointer-events-none disabled:opacity-50',
        isSelected && 'border-primary text-primary',
        className,
      )}
      onClick={(event) => {
        onClick?.(event)
        if (!event.defaultPrevented) select(value)
      }}
      onKeyDown={(event) => {
        onKeyDown?.(event)
        if (!event.defaultPrevented) handleRovingKeys(event, select)
      }}
      {...props}
    />
  )
}
TabsTrigger.displayName = 'TabsTrigger'

export interface TabsContentProps extends React.ComponentPropsWithoutRef<'div'> {
  value: string
}

/**
 * Panels stay mounted when inactive and are removed from the layout and the
 * accessibility tree with `hidden`, so a half-filled form survives a tab
 * switch. The `[&[hidden]]` variant keeps that true even if a caller's
 * `className` sets a `display` utility, which would otherwise win on specificity.
 */
function TabsContent({ className, value, ...props }: TabsContentProps) {
  const { value: selected, triggerId, panelId } = useTabsContext('TabsContent')
  const isSelected = selected === value

  return (
    <div
      role="tabpanel"
      id={panelId(value)}
      aria-labelledby={triggerId(value)}
      data-state={isSelected ? 'active' : 'inactive'}
      hidden={!isSelected}
      tabIndex={0}
      className={cn(
        'mt-4 focus-visible:outline-none',
        '[&[hidden]]:hidden',
        className,
      )}
      {...props}
    />
  )
}
TabsContent.displayName = 'TabsContent'

export { Tabs, TabsContent, TabsList, TabsTrigger }
