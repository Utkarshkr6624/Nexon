import { CircleAlert, CircleDot, GitBranch, Languages, Star } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import {
  NEVER_SCANNED_META,
  SCAN_STATUS_META,
} from '@/features/developer/components/developer-vocabulary'
import { TONE_BADGE_VARIANT } from '@/features/work/components/badge-tone'
import { cn } from '@/lib/utils'
import type { GitScanStatus } from '@/types/developer'
import type { StatusMeta } from '@/types/work'

/**
 * The small vocabulary chips the developer surface renders.
 *
 * **Every chip carries an icon *and* the word**, which is the same contract
 * `features/risk` discharges in `severity-badge.tsx` and for the same reason: a
 * small coloured pill is invisible to a screen-reader user, to a reader with a
 * colour vision deficiency, to a dark-mode reader where the tint shifts, and to
 * a printed page. The icons differ in *shape*, not only in hue, and the word is
 * the authority — nothing here infers a state the server did not send.
 *
 * `TONE_BADGE_VARIANT` is reused rather than re-derived, so a tone never names a
 * `Badge` variant in two places and the mapping cannot drift.
 *
 * The icons are `aria-hidden`: the word beside them already names the state, so
 * announcing "warning triangle, Scan failed" would duplicate the announcement
 * rather than add to it.
 */

export interface VocabularyBadgeProps {
  size?: 'sm' | 'default'
  className?: string
}

/**
 * Renders one `StatusMeta` as an icon plus its label.
 *
 * **A value outside the vocabulary still renders.** A payload this client has not
 * been taught about — a status added by a newer backend — must read as the word
 * the server sent rather than as a chip with no text at all, which a screen
 * reader would announce as nothing. This mirrors the risk surface's badge
 * helper, written for the same reason.
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

/**
 * How the last scan ended — or that there has not been one.
 *
 * `status` is `GitScanStatus | null` on the wire, and the null is not an error:
 * a repository can be registered and never read, because registration records
 * the path and does not open it. It gets its own chip rather than being folded
 * into "Scanned" or into "Scan failed".
 */
export function ScanStatusBadge({
  status,
  size,
  className,
}: VocabularyBadgeProps & { status: GitScanStatus | null }) {
  if (status === null) {
    return (
      <VocabularyBadge
        meta={NEVER_SCANNED_META}
        raw="never scanned"
        size={size}
        className={className}
      />
    )
  }

  return (
    <VocabularyBadge
      meta={SCAN_STATUS_META[status]}
      raw={String(status)}
      size={size}
      className={className}
    />
  )
}

/**
 * The repository's most common tracked extension.
 *
 * Null is a real state — nothing in the repository matches a recognised
 * extension — and it renders as its own chip saying exactly that, rather than as
 * "Other", which would be a category the scan never produced.
 */
export function LanguageBadge({
  language,
  size,
  className,
}: VocabularyBadgeProps & { language: string | null }) {
  if (language === null || language.trim().length === 0) {
    return (
      <Badge variant="secondary" className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}>
        <CircleDot aria-hidden="true" className={size === 'sm' ? 'size-2.5' : 'size-3'} />
        No tracked language
      </Badge>
    )
  }

  return (
    <Badge variant="secondary" className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}>
      <Languages aria-hidden="true" className={size === 'sm' ? 'size-2.5' : 'size-3'} />
      {language}
    </Badge>
  )
}

/**
 * One branch, and the two roles a branch can hold.
 *
 * `is_default` and `is_current` are rendered as separate chips rather than
 * merged, because on a detached HEAD the first is still true while the second is
 * not — a combined chip would have to hide one of them and lose the difference.
 */
export function BranchBadge({
  role,
  size,
  className,
}: VocabularyBadgeProps & { role: 'current' | 'default' }) {
  const meta: StatusMeta =
    role === 'current'
      ? {
          label: 'Checked out',
          icon: GitBranch,
          tone: 'info',
          description: 'The branch HEAD points at, as of the last scan.',
        }
      : {
          label: 'Default',
          icon: Star,
          tone: 'neutral',
          description: 'The branch git resolved as this repository’s default.',
        }

  return <VocabularyBadge meta={meta} raw={meta.label} size={size} className={className} />
}