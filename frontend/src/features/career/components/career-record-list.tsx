import type { ReactNode } from 'react'
import { Building2, ExternalLink, Info } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { CareerRecordKindBadge } from '@/features/career/components/career-badges'
import {
  CareerEmptyState,
  CareerRegionError,
  CareerStaleNotice,
} from '@/features/career/components/career-empty-state'
import {
  describeLinkLabel,
  describeRecordPeriod,
  isNavigableLink,
} from '@/features/career/components/career-format'
import { CAREER_RECORD_KIND_META } from '@/features/career/components/career-vocabulary'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'
import type { CareerExperienceRead, CareerRecordKind } from '@/types/learning'

/**
 * Education, work experience and certifications — the dated records.
 *
 * ## Records, not achievements
 *
 * These rows are the CV section, and they are deliberately never given the same
 * visual weight as the evidence timeline next door. "I worked here until March"
 * and "I shipped X" are different kinds of claim, and merging them would let a
 * job title read as an accomplishment.
 *
 * ## Nothing here was inferred
 *
 * Every field on these rows was typed by the user, and each null says so in its
 * own words rather than collapsing into a blank:
 *
 * - `ended_on: null` means the role or programme is **current**, which is a fact
 *   about the record — `describeRecordPeriod` says "current" rather than a dash.
 * - `started_on: null` means the user did not give a start date, and the row says
 *   "Until …" or "No dates given" rather than inventing one from `created_at`.
 * - `organisation: null` says nothing about the record's value, so the row simply
 *   does not name one.
 *
 * ## A link is only an anchor when it is a URL
 *
 * `url` is a string the user typed, so a value that does not parse is rendered
 * as plain text rather than as a dead `<a href>`.
 */
export interface CareerRecordRowProps {
  record: CareerExperienceRead
  className?: string
}

export function CareerRecordRow({ record, className }: CareerRecordRowProps) {
  const meta = CAREER_RECORD_KIND_META[record.kind]

  return (
    <li className={cn('min-w-0 space-y-2 py-3 first:pt-0 last:pb-0', className)}>
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 flex-1 text-sm font-medium leading-snug text-foreground">
          {record.title}
        </p>
        <CareerRecordKindBadge kind={record.kind} size="sm" />
      </div>

      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {record.organisation && (
          <span className="flex min-w-0 items-center gap-1.5">
            <Building2 aria-hidden="true" className="size-3 shrink-0" />
            <span className="truncate">{record.organisation}</span>
          </span>
        )}
        <span className="tabular-nums">{describeRecordPeriod(record.started_on, record.ended_on)}</span>
      </div>

      {record.description && (
        <p className="text-sm leading-relaxed text-muted-foreground">{record.description}</p>
      )}

      {record.url &&
        (isNavigableLink(record.url) ? (
          <a
            href={record.url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex min-w-0 items-center gap-1.5 truncate text-xs font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ExternalLink aria-hidden="true" className="size-3 shrink-0" />
            <span className="truncate">{describeLinkLabel(record.url)}</span>
          </a>
        ) : (
          <span
            className="block truncate text-xs text-muted-foreground"
            title="This was not recognised as a web address, so it is shown as the text you entered."
          >
            {record.url}
          </span>
        ))}

      <p className="flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
        <Info aria-hidden="true" className="mt-0.5 size-3 shrink-0" />
        <span>{meta.description}</span>
      </p>
    </li>
  )
}

/* ------------------------------------------------------------------ skeleton */

/**
 * The record list's loading silhouette.
 *
 * **No digits and no dates.** A grey "2021 — 2024" would be a career history
 * nobody entered, and this is the list where that fiction would do the most
 * damage — it is the part of a profile another person reads first.
 */
export function CareerRecordListSkeleton({
  count = 4,
  className,
}: {
  count?: number
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" className={cn('space-y-4', className)}>
      <span className="sr-only">Loading career records</span>
      {Array.from({ length: Math.max(1, count) }, (_, index) => (
        <div key={index} aria-hidden="true" className="min-w-0 space-y-2">
          <div className="flex items-baseline justify-between gap-3">
            <div className="h-3.5 w-2/5 animate-pulse rounded-md bg-muted" />
            <div className="h-4 w-20 shrink-0 animate-pulse rounded-md bg-muted" />
          </div>
          <div className="flex gap-3">
            <div className="h-3 w-28 animate-pulse rounded-md bg-muted" />
            <div className="h-3 w-24 animate-pulse rounded-md bg-muted" />
          </div>
          <div className="h-3 w-4/5 animate-pulse rounded-md bg-muted" />
        </div>
      ))}
    </div>
  )
}

export interface CareerRecordListProps {
  records: readonly CareerExperienceRead[]
  /** Narrows to one kind; the caller applies it to the filter, not to the rows. */
  kind?: CareerRecordKind | null
  isLoading?: boolean
  isStale?: boolean
  error?: ApiError | null
  onRetry?: () => void
  emptyReason?: string | null
  emptyAction?: ReactNode
  /** The backend's total, so a page of rows cannot be read as the whole list. */
  total?: number | null
  title?: string
  subtitle?: ReactNode
  skeletonCount?: number
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The record list card, with its loading, empty, error and stale states.
 *
 * **The order that arrived is the order shown.** These records are a chronology
 * the user built, and re-sorting them by date server-side-by-accident — or by a
 * client that assumed — would change the story a CV tells. The caller sorts if it
 * wants to.
 */
export function CareerRecordList({
  records,
  kind = null,
  isLoading = false,
  isStale = false,
  error = null,
  onRetry,
  emptyReason = null,
  emptyAction,
  total = null,
  title,
  subtitle,
  skeletonCount = 4,
  titleLevel = 'h3',
  className,
}: CareerRecordListProps) {
  const heading = title ?? (kind ? CAREER_RECORD_KIND_META[kind].label : 'Career records')

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-4">
        <CardTitle level={titleLevel}>{heading}</CardTitle>
        {subtitle && <CardDescription>{subtitle}</CardDescription>}
      </CardHeader>

      <CardContent className="space-y-3">
        <CareerStaleNotice isStale={isStale} subject="the record list" />

        {isLoading ? (
          <CareerRecordListSkeleton count={skeletonCount} />
        ) : error ? (
          <CareerRegionError error={error} onRetry={onRetry} subject="the record list" compact />
        ) : records.length === 0 ? (
          <CareerEmptyState
            variant="records"
            reason={emptyReason}
            action={emptyAction}
            className="py-4"
          />
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              {formatNumber(records.length)}{' '}
              {records.length === 1 ? 'record' : 'records'} shown. Everything here was entered by
              you, with the dates you gave.
            </p>
            <ul className="divide-y divide-border">
              {records.map((record) => (
                <CareerRecordRow key={record.id} record={record} />
              ))}
            </ul>
            {total !== null && total > records.length && (
              <p className="text-xs text-muted-foreground">
                Showing {formatNumber(records.length)} of {formatNumber(total)} records. This is a
                position in the list, not the whole set.
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}