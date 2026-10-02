import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { CornerDownLeft, Search } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Kbd } from '@/components/ui/kbd'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ALL_NAV_ITEMS, NAV_GROUPS } from '@/features/modules/catalog'
import type { ModuleDefinition } from '@/features/modules/catalog'
import { closeCommandPalette } from '@/features/command-palette/command-palette-store'
import { useDebouncedValue } from '@/hooks/use-debounce'
import { useCommandPalette, usesCommandKey } from '@/hooks/use-command-palette'
import { cn } from '@/lib/utils'

/** Stable ids so the input can point the screen reader at the active option. */
const LISTBOX_ID = 'command-palette-listbox'

function optionId(index: number): string {
  return `${LISTBOX_ID}-option-${index}`
}

function groupLabelFor(item: ModuleDefinition): string {
  const group = NAV_GROUPS.find((candidate) =>
    candidate.items.some((candidateItem) => candidateItem.to === item.to),
  )
  return group?.label ?? 'Platform'
}

function matches(item: ModuleDefinition, query: string): boolean {
  if (!query) return true
  const haystack = [item.label, item.summary, groupLabelFor(item), ...item.keywords]
    .join(' ')
    .toLowerCase()
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => haystack.includes(term))
}

interface PaletteResultsProps {
  items: ModuleDefinition[]
  activeIndex: number
  onActivate: (item: ModuleDefinition) => void
  onHover: (index: number) => void
}

function PaletteResults({ items, activeIndex, onActivate, onHover }: PaletteResultsProps) {
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = listRef.current?.querySelector<HTMLElement>('[data-active="true"]')
    node?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])

  if (items.length === 0) {
    return (
      <div className="px-4 py-10 text-center">
        <p className="text-sm font-medium text-foreground">No destinations match</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Try a module name, or search for work, notes or settings.
        </p>
      </div>
    )
  }

  return (
    <div ref={listRef} id={LISTBOX_ID} role="listbox" aria-label="Destinations" className="p-1.5">
      {items.map((item, index) => {
        const Icon = item.icon
        const active = index === activeIndex
        return (
          // Not focusable: the input keeps DOM focus and names the active option
          // through `aria-activedescendant`, which is the combobox pattern.
          <div
            key={item.to}
            id={optionId(index)}
            role="option"
            aria-selected={active}
            data-active={active}
            onMouseMove={() => onHover(index)}
            onClick={() => onActivate(item)}
            className={cn(
              'flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-left transition-colors duration-150 ease-out',
              active ? 'bg-accent text-accent-foreground' : 'text-foreground',
            )}
          >
            <span
              className={cn(
                'flex size-8 shrink-0 items-center justify-center rounded-md border border-border',
                active ? 'bg-background text-primary' : 'bg-muted text-muted-foreground',
              )}
            >
              <Icon className="size-4" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2">
                <span className="truncate text-sm font-medium">{item.label}</span>
                <span className="text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
                  {groupLabelFor(item)}
                </span>
              </span>
              <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                {item.summary}
              </span>
            </span>
            {active && (
              <CornerDownLeft className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
            )}
          </div>
        )
      })}
    </div>
  )
}

/**
 * Mounted only while the palette is open, so the query and selection start
 * clean on every invocation without an effect to reset them.
 */
function PaletteDialog() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [selection, setSelection] = useState({ query: '', index: 0 })
  const debouncedQuery = useDebouncedValue(query, 120)
  const inputRef = useRef<HTMLInputElement>(null)

  // The element focused before the palette opened; restored on unmount.
  const previouslyFocused = useRef<HTMLElement | null>(
    typeof document === 'undefined' ? null : (document.activeElement as HTMLElement | null),
  )

  const items = useMemo(
    () => ALL_NAV_ITEMS.filter((item) => matches(item, debouncedQuery)),
    [debouncedQuery],
  )

  // A stale index (e.g. from a longer result list) must never point nowhere.
  const activeIndex = selection.query === debouncedQuery ? Math.min(selection.index, items.length - 1) : 0

  // The "No destinations match" branch renders no listbox at all.
  const hasResults = items.length > 0

  const setActiveIndex = useCallback(
    (index: number) => setSelection({ query: debouncedQuery, index }),
    [debouncedQuery],
  )

  const activate = useCallback(
    (item: ModuleDefinition) => {
      closeCommandPalette()
      navigate(item.to)
    },
    [navigate],
  )

  useEffect(() => {
    const frame = requestAnimationFrame(() => inputRef.current?.focus())
    return () => cancelAnimationFrame(frame)
  }, [])

  useEffect(() => {
    const restoreFocusTo = previouslyFocused.current
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
      restoreFocusTo?.focus?.()
    }
  }, [])

  function onKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeCommandPalette()
      return
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActiveIndex(items.length === 0 ? 0 : (activeIndex + 1) % items.length)
      return
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActiveIndex(
        items.length === 0 ? 0 : (activeIndex - 1 + items.length) % items.length,
      )
      return
    }
    if (event.key === 'Enter') {
      const target = items[activeIndex]
      if (target) {
        event.preventDefault()
        activate(target)
      }
      return
    }
    if (event.key === 'Tab') {
      // Keep focus inside the palette while it is modal.
      event.preventDefault()
      inputRef.current?.focus()
    }
  }

  const modKey = usesCommandKey() ? '⌘' : 'Ctrl'

  return createPortal(
    <div
      className="fixed inset-0 z-50 animate-in fade-in-0 duration-150 ease-out"
      onKeyDown={onKeyDown}
    >
      <button
        type="button"
        aria-label="Close command palette"
        onClick={closeCommandPalette}
        className="absolute inset-0 h-full w-full cursor-default bg-foreground/20 backdrop-blur-[1px]"
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className={cn(
          'absolute left-1/2 top-[12vh] w-[min(560px,calc(100vw-32px))] -translate-x-1/2',
          'animate-in fade-in-0 zoom-in-95 overflow-hidden rounded-lg border border-border bg-popover shadow-lg duration-150 ease-out',
        )}
      >
        <div className="flex items-center gap-2.5 border-b border-border px-3.5">
          <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Jump to a module…"
            aria-label="Filter destinations"
            role="combobox"
            aria-expanded={hasResults}
            // Both are only meaningful against a rendered listbox. The
            // "No destinations match" branch has none, so pointing at the id
            // would leave `aria-controls` and `aria-activedescendant` naming
            // an element that does not exist.
            aria-controls={hasResults ? LISTBOX_ID : undefined}
            aria-autocomplete="list"
            aria-activedescendant={
              hasResults ? optionId(activeIndex) : undefined
            }
            className="h-12 w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
          />
          <Badge variant="outline" className="hidden shrink-0 font-normal sm:inline-flex">
            {items.length} results
          </Badge>
        </div>

        <ScrollArea className="max-h-[min(360px,50vh)]">
          <PaletteResults
            items={items}
            activeIndex={activeIndex}
            onHover={setActiveIndex}
            onActivate={activate}
          />
        </ScrollArea>

        <div className="flex items-center gap-3 border-t border-border px-3.5 py-2 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd> navigate
          </span>
          <span className="flex items-center gap-1">
            <Kbd>↵</Kbd> open
          </span>
          <span className="ml-auto flex items-center gap-1">
            <Kbd>{modKey}</Kbd>
            <Kbd>K</Kbd>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  )
}

/**
 * Global destination palette, opened with ⌘K / Ctrl+K. It is the navigational
 * half of the search story: Phase 1 filters destinations, and the cross-module
 * query surface arrives with the Search module in Phase 4.
 */
export function CommandPalette() {
  const { open } = useCommandPalette()
  return open ? <PaletteDialog /> : null
}