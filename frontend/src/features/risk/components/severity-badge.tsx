import {
  BadgeCheck,
  Ban,
  CircleAlert,
  CircleCheck,
  CircleDot,
  Clock,
  Eye,
  OctagonAlert,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  X,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
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
 * natural move. It is also the move that fails first — a reader in dark mode,
 * a reader with a colour vision deficiency, a screen-reader user and a printed
 * page all get four indistinguishable small pills.
 *
 * The icons are chosen so the four bands differ in *shape*, not only in hue:
 * `OctagonAlert` → `TriangleAlert` → `CircleAlert` → `ShieldCheck` is a
 * silhouette ladder a reader can order without seeing the colour at all. A
 * reviewer checking this requirement should be able to desaturate the page and
 * still rank the cards.
 *
 * **The words are the authority, the icon is the reinforcement.** Nothing here
 * infers a band from a score: the backend derives `severity` from `score`, and
 * this module renders the band it was handed. A chip that recomputed the band
 * would be a second implementation of `risk_severity_for` on the client, and the
 * two would eventually disagree.
 *
 * `StatusMeta` is reused from the work types rather than a local shape invented
 * here, so the chip, its tone token and its description cannot drift apart, and
 * `TONE_BADGE_VARIANT` remains the only place a tone names a `Badge` variant —
 * the same mapping the knowledge surface already goes through.
 */

/**
 * The four severity bands, described by the thresholds the backend actually
 * uses. `DEFAULT_SEVERITY_THRESHOLDS` in `app/services/risk/scoring.py` floors
 * them at 75 / 50 / 25, so the descriptions state those numbers rather than
 * an impression: a reader who wants to know why a row landed in a band can check
 * the score on the meter against the sentence on the chip.
 */
export const SEVERITY_META: Record<RiskSeverity, StatusMeta> = {
  critical: {
    label: 'Critical',
    icon: OctagonAlert,
    tone: 'danger',
    description: 'A score of 75 or above out of 100.',
  },
  high: {
    label: 'High',
    icon: TriangleAlert,
    tone: 'warning',
    description: 'A score from 50 to 74 out of 100.',
  },
  medium: {
    label: 'Medium',
    icon: CircleAlert,
    tone: 'info',
    description: 'A score from 25 to 49 out of 100.',
  },
  low: {
    label: 'Low',
    icon: ShieldCheck,
    tone: 'neutral',
    description: 'A score below 25 out of 100.',
  },
}

/**
 * Where a risk sits in its lifecycle. The two live states share a tone on
 * purpose — `acknowledged` is not calmer than `active`, only quieter — and the
 * two terminal states share `success`, because a closed risk is a completed
 * question rather than a failure, whatever the answer was.
 */
export const RISK_STATUS_META: Record<RiskStatus, StatusMeta> = {
  active: {
    label: 'Active',
    icon: CircleDot,
    tone: 'info',
    description: 'Detected and not yet answered.',
  },
  acknowledged: {
    label: 'Acknowledged',
    icon: Eye,
    tone: 'info',
    description: 'Seen and still true. It keeps being re-checked but no longer asks for attention.',
  },
  resolved: {
    label: 'Resolved',
    icon: CircleCheck,
    tone: 'success',
    description: 'The condition behind it is no longer present.',
  },
  dismissed: {
    label: 'Dismissed',
    icon: Ban,
    tone: 'neutral',
    description: 'Closed as not applying.',
  },
}

/**
 * Where a suggestion sits. `expired` is a service decision rather than a clock:
 * the risk behind the suggestion was resolved, so the suggestion became moot,
 * and recording that is more useful than recording that it got old.
 */
export const RECOMMENDATION_STATUS_META: Record<RecommendationStatus, StatusMeta> = {
  new: {
    label: 'New',
    icon: Sparkles,
    tone: 'info',
    description: 'Raised and not yet read.',
  },
  viewed: {
    label: 'Viewed',
    icon: Eye,
    tone: 'info',
    description: 'Read and not yet answered.',
  },
  accepted: {
    label: 'Accepted',
    icon: CircleCheck,
    tone: 'success',
    description: 'Taken on as work to do.',
  },
  rejected: {
    label: 'Rejected',
    icon: X,
    tone: 'neutral',
    description: 'Declined. The underlying condition may still be true.',
  },
  completed: {
    label: 'Completed',
    icon: BadgeCheck,
    tone: 'success',
    description: 'Acted on.',
  },
  expired: {
    label: 'Expired',
    icon: Clock,
    tone: 'neutral',
    description: 'The risk behind it was resolved, so it no longer applies.',
  },
}

export interface VocabularyBadgeProps {
  size?: 'sm' | 'default'
  className?: string
}

/**
 * Renders one `StatusMeta` as an icon plus its label.
 *
 * **A value outside the vocabulary still renders.** A payload the client has
 * not been taught about — a new status from a newer backend — must read as the
 * word the server sent rather than as a chip with no text, which a screen reader
 * would announce as nothing at all. This mirrors the knowledge surface's badge
 * helper, which was written for the same reason.
 *
 * The icon is `aria-hidden`: the word beside it already names the state, so
 * announcing "warning triangle, High" would be a duplicated announcement rather
 * than extra information.
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
