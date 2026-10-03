import type { ReactNode } from 'react'
import { FolderKanban, GitBranch, Sparkles } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { CareerEvidenceTypeBadge, EvidenceSourceBadge } from '@/features/career/components/career-badges'
import {
  CareerEmptyState,
  CareerRegionError,
  CareerStaleNotice,
} from '@/features/career/components/career-empty-state'
import {
  countFromRecord,
  formatCareerDate,
} from '@/features/career/components/career-format'
import {
  CAREER_EVIDENCE_TYPE_META,
  CAREER_EVIDENCE_TYPE_ORDER,
} from '@/features/career/components/career-vocabulary'
import { formatNumber } from '@/features/analytics/format'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'
import type { CareerEvidenceRead, CareerEvidenceType, UUIDString } from '@/types/learning'

/**
 * Everything on the profile, grouped by what kind of thing it is.
 *
 * ## Grouping, not ranking
 *
 * Rows are grouped under their evidence type so a reader can see, at a glance,
 * *what kinds* of thing this profile actually contains — a page of nine
 * unlabelled cards cannot answer that, and the answer is what a person deciding
 * about the profile is really looking for. **No group is ordered by weight**: the
 * order is fixed by the vocabulary, and nothing here says one kind of evidence is
 * worth more than another. A single `Achievement` is not a weakness and a
 * hundred `Repository activity` rows are not a strength.
 *
 * ## The date is not nullable
 *
 * `occurred_on` is required on the wire because evidence with no date could not
 * be ordered, so there is no absent-date state to render here — which is worth
 * noting, because every other field on this surface is nullable and there is a
 * habit to break.
 *
 * ## Provenance is always on the row
 *
 * Each row carries its source chip: `Added by you` or `Recorded by NEXUS`. The
 * three nullable links (`project_id`, `skill_id`, `repository_id`) are shown as
 * names when the caller resolved them and as an honest "no longer linked" when
 * the row pointed at something since deleted — `ON DELETE SET NULL` means that is
 * an ordinary state for a durable record, not damage.
 */
export interface PortfolioEvidenceRowProps {
  evidence: CareerEvidenceRead
  projectName?: string | null
  skillName?: string | null
  repositoryName?: string | null
  className?: string
}

export function PortfolioEvidenceRow({
  evidence,
  projectName = null,
  skillName = null,
  repositoryName = null,
  className,
}: PortfolioEvidenceRowProps) {
  return (
    <li className={cn('min-w-0 space-y-2 py-3 first:pt-0 last:pb-0', className)}>
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 flex-1 text-sm font-medium leading-snug text-foreground">
          {evidence.title}
        </p>
        <p className="shrink-0 text-xs tabular-nums text-muted-foreground">
          <time dateTime={evidence.occurred_on}>{formatCareerDate(evidence.occurred_on)}</time>
        </p>
      </div>

      {evidence.description && (
        <p className="text-sm leading-relaxed text-muted-foreground">{evidence.description}</p>
      )}

      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5">
        <CareerEvidenceTypeBadge evidenceType={evidence.evidence_type} size="sm" />
        <EvidenceSourceBadge source={evidence.source} size="sm" />
      </div>

      <ul className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {evidence.project_id && (
          <li className="flex min-w-0 items-center gap-1.5">
            <FolderKanban aria-hidden="true" className="size-3 shrink-0" />
            <span className="truncate">
              {projectName ?? 'Linked project no longer available'}
            </span>
          </li>
        )}
        {evidence.skill_id && (
          <li className="flex min-w-0 items-center gap-1.5">
            <Sparkles aria-hidden="true" className="size-3 shrink-0" />
            <span className="truncate">{skillName ?? 'Linked skill no longer tracked'}</span>
          </li>
        )}
        {evidence.repository_id && (
          <li className="flex min-w-0 items-center gap-1.5">
            <GitBranch aria-hidden="true" className="size-3 shrink-0" />
            <span className="truncate">
              {repositoryName ?? 'Linked repository no longer registered'}
            </span>
          </li>
        )}
        {!evidence.project_id && !evidence.skill_id && !evidence.repository_id && (
          <li>Not linked to a project, skill or repository.</li>
        )}
      </ul>
    </li>
  )
}

/* ------------------------------------------------------------------- grouping */

/**
 * Groups rows by evidence type, known types first in the vocabulary's order and
 * anything this client has no vocabulary for **appended rather than dropped**.
 *
 * A type added by a newer backend must still be shown: filtering it out would
 * quietly hide a person's evidence because this client had not caught up, which
 * is the one failure mode worse than an unstyled group heading. Its rows are
 * labelled with the word the server sent.
 */
function groupEvidenceByType(
  evidence: readonly CareerEvidenceRead[],
): { type: string; rows: CareerEvidenceRead[] }[] {
  const seen = new Set<string>()
  for (const row of evidence) seen.add(row.evidence_type)

  const known = CAREER_EVIDENCE_TYPE_ORDER.filter((type) => seen.has(type))
  const knownTypes = new Set<string>(CAREER_EVIDENCE_TYPE_ORDER)
  const extra = [...seen].filter((type) => !knownTypes.has(type)).sort()

  return [...known, ...extra].map((type) => ({
    type,
    rows: evidence.filter((row) => row.evidence_type === type),
  }))
}

/** The vocabulary entry for a type, or a neutral fallback for an unknown one. */
function evidenceTypeMeta(type: string): { label: string; description: string } {
  const known = CAREER_EVIDENCE_TYPE_META[type as CareerEvidenceType]
  if (known) return known
  return {
    label: type,
    description:
      'A type this build of NEXUS has no vocabulary for. The row is shown with the name the ' +
      'server sent, and nothing is inferred about what it claims.',
  }
}

/* ------------------------------------------------------------------ skeleton */

/**
 * The timeline's loading silhouette.
 *
 * Grouped the way the real timeline is — a label block, then two rows of title
 * and date lines — so the section headings' space is reserved. **No digits and
 * no dates**: a grey "01 Jan 2025" would be a date the user never entered.
 */
export function PortfolioEvidenceTimelineSkeleton({
  groups = 2,
  rowsPerGroup = 2,
  className,
}: {
  groups?: number
  rowsPerGroup?: number
  className?: string
}) {
  return (
    <div role="status" aria-busy="true" className={cn('space-y-6', className)}>
      <span className="sr-only">Loading portfolio evidence</span>
      {Array.from({ length: Math.max(1, groups) }, (_, group) => (
        <div key={group} aria-hidden="true" className="min-w-0 space-y-3">
          <div className="h-2.5 w-40 animate-pulse rounded-md bg-muted" />
          <div className="space-y-4">
            {Array.from({ length: Math.max(1, rowsPerGroup) }, (_, row) => (
              <div key={row} className="min-w-0 space-y-2">
                <div className="flex items-baseline justify-between gap-3">
                  <div className="h-3.5 w-3/5 animate-pulse rounded-md bg-muted" />
                  <div className="h-3 w-20 shrink-0 animate-pulse rounded-md bg-muted" />
                </div>
                <div className="flex gap-2">
                  <div className="h-4 w-28 animate-pulse rounded-md bg-muted" />
                  <div className="h-4 w-20 animate-pulse rounded-md bg-muted" />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

export interface PortfolioEvidenceTimelineProps {
  evidence: readonly CareerEvidenceRead[]
  /** Counts keyed by evidence type across **every** row, not just this page. */
  byType?: Record<string, number> | null
  isLoading?: boolean
  isStale?: boolean
  error?: ApiError | null
  onRetry?: () => void
  emptyReason?: string | null
  emptyAction?: ReactNode
  projectName?: (projectId: UUIDString) => string | null
  skillName?: (skillId: UUIDString) => string | null
  repositoryName?: (repositoryId: UUIDString) => string | null
  /** The backend's total, so a page of rows cannot be read as the whole set. */
  total?: number | null
  title?: string
  subtitle?: ReactNode
  skeletonCount?: number
  titleLevel?: 'h3' | 'h4'
  className?: string
}

/**
 * The evidence timeline card, with its loading, empty, error and stale states.
 *
 * The group headers read their count from `byType`, which the backend computes
 * across every matching row rather than the page in hand — so a header cannot
 * quote a total the pager contradicts. **When the response does not carry a
 * count for a type, the header says how many rows are *shown*** rather than
 * substituting a zero, because a missing key is not a measured zero.
 */
export function PortfolioEvidenceTimeline({
  evidence,
  byType = null,
  isLoading = false,
  isStale = false,
  error = null,
  onRetry,
  emptyReason = null,
  emptyAction,
  projectName,
  skillName,
  repositoryName,
  total = null,
  title = 'Portfolio evidence',
  subtitle,
  skeletonCount = 2,
  titleLevel = 'h3',
  className,
}: PortfolioEvidenceTimelineProps) {
  const nameForProject = projectName ?? (() => null)
  const nameForSkill = skillName ?? (() => null)
  const nameForRepository = repositoryName ?? (() => null)

  const grouped = groupEvidenceByType(evidence)

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="pb-4">
        <CardTitle level={titleLevel}>{title}</CardTitle>
        {subtitle && <CardDescription>{subtitle}</CardDescription>}
      </CardHeader>

      <CardContent className="space-y-3">
        <CareerStaleNotice isStale={isStale} subject="the evidence timeline" />

        {isLoading ? (
          <PortfolioEvidenceTimelineSkeleton groups={skeletonCount} />
        ) : error ? (
          <CareerRegionError error={error} onRetry={onRetry} subject="the evidence timeline" compact />
        ) : evidence.length === 0 ? (
          <CareerEmptyState
            variant="evidence"
            reason={emptyReason}
            action={emptyAction}
            className="py-4"
          />
        ) : (
          <>
            <p className="text-xs text-muted-foreground">
              {formatNumber(evidence.length)} {evidence.length === 1 ? 'row' : 'rows'} shown across{' '}
              {formatNumber(grouped.length)} {grouped.length === 1 ? 'kind' : 'kinds'}, newest first
              within each.
            </p>

            {grouped.map((group) => {
              const meta = evidenceTypeMeta(group.type)
              const totalForType = countFromRecord(byType, group.type)
              return (
                <section
                  key={group.type}
                  className="min-w-0 space-y-3"
                  aria-labelledby={`evidence-${group.type}`}
                >
                  <h4
                    id={`evidence-${group.type}`}
                    className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
                  >
                    {meta.label}
                    <span className="ml-2 normal-case tracking-normal">
                      {totalForType === null
                        ? `${formatNumber(group.rows.length)} shown`
                        : `${formatNumber(totalForType)} in total`}
                    </span>
                  </h4>
                  <p className="text-xs leading-relaxed text-muted-foreground">{meta.description}</p>
                  <ul className="divide-y divide-border">
                    {group.rows.map((row) => (
                      <PortfolioEvidenceRow
                        key={row.id}
                        evidence={row}
                        projectName={row.project_id ? nameForProject(row.project_id) : null}
                        skillName={row.skill_id ? nameForSkill(row.skill_id) : null}
                        repositoryName={row.repository_id ? nameForRepository(row.repository_id) : null}
                      />
                    ))}
                  </ul>
                </section>
              )
            })}

            <PortfolioEvidenceScopeNote total={total} shown={evidence.length} />
          </>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * Shown in a timeline with no pagination control, so a reader knows it is partial.
 *
 * `total` is the backend's figure and `shown` is the length of the page that
 * arrived; the two disagree on every paginated read, and a timeline that said
 * nothing about it would let a reader mistake page one for the whole set.
 */
export function PortfolioEvidenceScopeNote({
  total,
  shown,
}: {
  total: number | null
  shown: number
}) {
  if (total === null || total <= shown) {
    return <p className="text-xs text-muted-foreground">Everything on file is shown here.</p>
  }

  return (
    <p className="text-xs text-muted-foreground">
      Showing {formatNumber(shown)} of {formatNumber(total)} recorded rows. This is a position in
      the list, not the whole set.
    </p>
  )
}