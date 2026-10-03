import { CircleAlert } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import {
  CAREER_EVIDENCE_TYPE_META,
  CAREER_LEVEL_SOURCE_META,
  CAREER_RECORD_KIND_META,
  evidenceSourceMeta,
} from '@/features/career/components/career-vocabulary'
import { TONE_BADGE_VARIANT } from '@/features/work/components/badge-tone'
import { cn } from '@/lib/utils'
import type { CareerEvidenceType, CareerRecordKind, SkillLevelSource } from '@/types/learning'

/**
 * The small vocabulary chips the career surface renders.
 *
 * **Every chip carries an icon *and* the word**, which is the same contract
 * `features/risk`, `features/developer` and `features/learning` discharge and for
 * the same reason: a small coloured pill is invisible to a screen-reader user, to
 * a reader with a colour vision deficiency, to a dark-mode reader where the tint
 * shifts, and to a printed page. The icons differ in *shape*, not only in hue,
 * and the word is the authority.
 *
 * `TONE_BADGE_VARIANT` is reused rather than re-derived, so a tone never names a
 * `Badge` variant in two places and the mapping cannot drift.
 *
 * The **source chip is the important one**: `Added by you` and `Recorded by
 * NEXUS` are different provenances and a reader deciding whether to trust a
 * profile line needs to see which they are looking at.
 */
export interface CareerBadgeProps {
  size?: 'sm' | 'default'
  className?: string
}

/**
 * Renders one vocabulary entry as an icon plus its label.
 *
 * **A value outside the vocabulary still renders.** A payload this client has
 * not been taught about — an evidence type added by a newer backend — must read
 * as the word the server sent rather than as a chip with no text at all, which a
 * screen reader would announce as nothing.
 */
function VocabularyBadge({
  meta,
  raw,
  size = 'default',
  className,
}: CareerBadgeProps & { meta: { label: string; icon: typeof CircleAlert; description?: string } | undefined; raw: string }) {
  const resolved = meta ?? { label: raw, icon: CircleAlert }
  const Icon = resolved.icon
  const iconClass = size === 'sm' ? 'size-2.5' : 'size-3'

  return (
    <Badge
      variant="secondary"
      className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}
      title={meta?.description}
    >
      <Icon aria-hidden="true" className={iconClass} />
      {resolved.label}
    </Badge>
  )
}

/**
 * What kind of thing a piece of evidence is.
 *
 * The chip's `title` is the type's own sentence, which is what stops a reader
 * taking `Repository activity` for a count of things delivered: the qualifier
 * "code events" is right there where they hover it.
 */
export function CareerEvidenceTypeBadge({
  evidenceType,
  size,
  className,
}: CareerBadgeProps & { evidenceType: CareerEvidenceType }) {
  return (
    <VocabularyBadge
      meta={CAREER_EVIDENCE_TYPE_META[evidenceType]}
      raw={String(evidenceType)}
      size={size}
      className={className}
    />
  )
}

/**
 * Which section of a CV a dated record belongs to.
 *
 * Education, experience and certification are the record section, kept visually
 * and verbally apart from the achievement section.
 */
export function CareerRecordKindBadge({
  kind,
  size,
  className,
}: CareerBadgeProps & { kind: CareerRecordKind }) {
  return (
    <VocabularyBadge
      meta={CAREER_RECORD_KIND_META[kind]}
      raw={String(kind)}
      size={size}
      className={className}
    />
  )
}

/**
 * Who said a skill level.
 *
 * Rendered beside **every** level this surface shows, in the overview grid and
 * in the development-areas panel alike. It is not decoration attached to a
 * number — it is what makes the number sayable, and there is deliberately no prop
 * to switch it off.
 */
export function LevelOriginBadge({
  source,
  size,
  className,
}: CareerBadgeProps & { source: SkillLevelSource }) {
  const meta = CAREER_LEVEL_SOURCE_META[source]
  const Icon = meta.icon
  const iconClass = size === 'sm' ? 'size-2.5' : 'size-3'

  return (
    <Badge
      variant={TONE_BADGE_VARIANT[meta.tone]}
      className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}
      title={meta.description}
    >
      <Icon aria-hidden="true" className={iconClass} />
      {meta.label}
    </Badge>
  )
}

/**
 * Where an evidence row came from.
 *
 * `manual` says "Added by you"; anything else says "Recorded by NEXUS". **The
 * two are never collapsed into a single "source" chip**, because the difference
 * between a claim the person is making and an inference NEXUS drew from a record
 * they created is the difference between evidence and generated text.
 */
export function EvidenceSourceBadge({
  source,
  size,
  className,
}: CareerBadgeProps & { source: string }) {
  const meta = evidenceSourceMeta(source)
  const Icon = meta.icon
  const iconClass = size === 'sm' ? 'size-2.5' : 'size-3'

  return (
    <Badge
      variant={TONE_BADGE_VARIANT[meta.tone]}
      className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}
      title={meta.phrase}
    >
      <Icon aria-hidden="true" className={iconClass} />
      {meta.label}
    </Badge>
  )
}