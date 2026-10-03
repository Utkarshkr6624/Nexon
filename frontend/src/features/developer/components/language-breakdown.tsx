import { useMemo, type ReactNode } from 'react'

import { DeveloperEmptyState, DeveloperStaleNotice } from '@/features/developer/components/developer-empty-state'
import {
  languageUnitNoun,
  type LanguageCount,
} from '@/features/developer/components/developer-format'
import { ChartShell } from '@/features/analytics/components/chart-shell'
import { AnalyticsBarChart, LazyChart } from '@/features/analytics/components/lazy-charts'
import type { ChartRow } from '@/features/analytics/components/trend-chart'
import { cn } from '@/lib/utils'

/**
 * The language distribution, as horizontal bars.
 *
 * ## The unit travels with the data
 *
 * There are two real language breakdowns on this surface and they are not the
 * same measurement: a repository's **tracked-file** distribution (counted by file
 * extension across one work tree) and a repository list's **primary-language**
 * distribution (one row per repository, grouped by its most common extension).
 * A single axis labelled "files" applied to both would misdescribe the second, so
 * every row carries its own `unit` and the axis noun is read from the data.
 *
 * ## No "other" bucket
 *
 * The backend deliberately does not bucket unrecognised extensions — a bucket of
 * files whose extension the scan did not recognise is a category, not a
 * language, and calling it one would invent something git never reported. So
 * this chart shows only languages git actually recognised, and its empty copy
 * says exactly that rather than implying the repository has no code.
 *
 * **Horizontal because the labels are words.** Six language names on a vertical
 * axis rotate into unreadable slanted text; `AnalyticsBarChart`'s
 * `orientation="horizontal"` reads them top-to-bottom intact, which is a prop on
 * the shared component rather than a second chart to own here.
 *
 * Nothing is drawn in this file: the bars are the shared analytics bar chart
 * inside its lazy boundary, so this surface does not pay recharts' module cost
 * for a panel most pages scroll past.
 *
 * `LanguageCount` and the helpers that build one live in `developer-format` —
 * they are pure functions, and `react-refresh/only-export-components` forbids a
 * module exporting a component beside a non-component.
 */

export type { LanguageCount }

const EMPTY_REASON =
  'Languages are counted from the file extensions git tracks. A repository whose files ' +
  'use extensions outside the recognised set contributes nothing here — there is no "other" ' +
  'bucket, because that would be a category rather than a language.'

export interface LanguageBreakdownProps {
  languages: readonly LanguageCount[]
  title?: string
  subtitle?: ReactNode
  isLoading?: boolean
  /** A refetch is in flight behind bars already on screen. */
  isStale?: boolean
  className?: string
}

export function LanguageBreakdown({
  languages,
  title = 'Language distribution',
  subtitle,
  isLoading = false,
  isStale = false,
  className,
}: LanguageBreakdownProps) {
  const rows = useMemo<ChartRow[]>(
    () => languages.map((entry) => ({ label: entry.language, count: entry.count })),
    [languages],
  )

  const unit = languages[0]?.unit ?? 'files'
  const noun = languageUnitNoun(unit)
  const text = typeof subtitle === 'string' ? subtitle : undefined
  const empty = languages.length === 0

  return (
    <div className={cn('space-y-3', className)}>
      <DeveloperStaleNotice isStale={isStale} subject="the language breakdown" />

      <LazyChart title={title} subtitle={text}>
        {empty ? (
          // `ChartShell` rather than the bar chart's own empty branch, so this
          // surface keeps the shared frame and height and only the wording
          // differs: a language breakdown with nothing in it is this surface's
          // "Not enough data yet." state, not the analytics one.
          <ChartShell
            title={title}
            subtitle={text}
            empty={
              <DeveloperEmptyState
                variant="languages"
                reason={EMPTY_REASON}
                className="h-full"
              />
            }
          >
            {null}
          </ChartShell>
        ) : (
          <AnalyticsBarChart
            title={title}
            subtitle={text}
            data={rows}
            series={[{ key: 'count', label: noun, colorIndex: 0, unit: 'count' }]}
            xKey="label"
            orientation="horizontal"
            colorByCategory
            isLoading={isLoading}
          />
        )}
      </LazyChart>
    </div>
  )
}