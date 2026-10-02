import { CircleAlert } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import {
  RECOMMENDATION_STATUS_META,
  RISK_STATUS_META,
  SEVERITY_META,
} from '@/features/risk/components/risk-vocabulary'
import { TONE_BADGE_VARIANT } from '@/features/work/components/badge-tone'
import { cn } from '@/lib/utils'
import type {
  RecommendationPriority,
  RecommendationStatus,
  RiskSeverity,
  RiskStatus,
} from '@/types/risk'
import type { StatusMeta } from '@/types/work'

/**
 * The small vocabulary chips the risk surface renders.
 *
 * **Every chip on this surface carries an icon *and* the word.** That is a brief
 * requirement, and this module is where it is discharged: the four severity
 * bands are otherwise the most tempting place in the app to encode meaning in
 * colour alone, because a red/amber/blue/grey ladder is the design system's
 * natural move. It is also the move that fails first — a reader in dark mode, a
 * reader with a colour vision deficiency, a screen-reader user and a printed
 * page all get four indistinguishable small pills.
 *
 * The icons differ in *shape*, not only in hue, and the words are the authority
 * while the icon reinforces: nothing here infers a band from a score. The
 * backend derives `severity` from `score`, and a chip that recomputed the band
 * would be a second implementation of `risk_severity_for` on the client, and
 * the two would eventually disagree.
 *
 * The icon is `aria-hidden`. The word beside it already names the state, so
 * announcing "warning triangle, High" would be a duplicated announcement rather
 * than extra information.
 *
 * `StatusMeta` is reused from the work types rather than a local shape invented
 * here, so a chip, its tone token and its description cannot drift apart, and
 * `TONE_BADGE_VARIANT` remains the only place a tone names a `Badge` variant —
 * the same mapping the knowledge surface already goes through.
 */

export interface VocabularyBadgeProps {
  size?: 'sm' | 'default'
  className?: string
}

/**
 * Renders one `StatusMeta` as an icon plus its label.
 *
 * **A value outside the vocabulary still renders.** A payload this client has
 * not been taught about — a new status from a newer backend — must read as the
 * word the server sent rather than as a chip with no text, which a screen reader
 * would announce as nothing at all. This mirrors the knowledge surface's badge
 * helper, which was written for the same reason.
 */
function VocabularyBadge({
  meta,
  raw,
  size = 'default',
  className,
}: VocabularyBadgeProps & { meta: StatusMeta | undefined; raw: string }) {
  const resolved = meta ?? { label: raw, icon: CircleAlert, tone: 'neutral' as const }
  const Icon = resolved.icon
  const iconClass = size === 'sm' ? 'size-2.5' : 'size-3'

  return (
    <Badge
      variant={TONE_BADGE_VARIANT[resolved.tone]}
      className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}
      title={meta?.description}
    >
      <Icon aria-hidden="true" className={iconClass} />
      {resolved.label}
    </Badge>
  )
}

export type SeverityBadgeProps = VocabularyBadgeProps

/**
 * The severity band, as an icon and the band name.
 *
 * `RecommendationPriority` is accepted for the same value: the backend derives
 * priority from severity, and the types deliberately declare the two ladders
 * identically, so one chip can serve a risk row and a suggestion row without
 * the caller converting between two identical unions.
 */
export function SeverityBadge({
  severity,
  size,
  className,
}: VocabularyBadgeProps & { severity: RiskSeverity | RecommendationPriority }) {
  return (
    <VocabularyBadge
      meta={SEVERITY_META[severity as RiskSeverity]}
      raw={String(severity)}
      size={size}
      className={className}
    />
  )
}

/** Where a risk sits in its lifecycle. Carries the same icon-and-word contract. */
export function RiskStatusBadge({
  status,
  size,
  className,
}: VocabularyBadgeProps & { status: RiskStatus }) {
  return (
    <VocabularyBadge meta={RISK_STATUS_META[status]} raw={String(status)} size={size} className={className} />
  )
}

/** Where a suggestion sits. Carries the same icon-and-word contract. */
export function RecommendationStatusBadge({
  status,
  size,
  className,
}: VocabularyBadgeProps & { status: RecommendationStatus }) {
  return (
    <VocabularyBadge
      meta={RECOMMENDATION_STATUS_META[status]}
      raw={String(status)}
      size={size}
      className={className}
    />
  )
}
