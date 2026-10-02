import { Badge } from '@/components/ui/badge'
import { TONE_BADGE_VARIANT } from '@/features/work/components/badge-tone'
import { cn } from '@/lib/utils'
import {
  KNOWLEDGE_ENTITY_META,
  LINK_TYPE_META,
  NOTE_STATUS_META,
  RESOURCE_TYPE_META,
} from '@/types/knowledge'
import type { StatusMeta } from '@/types/work'
import type {
  KnowledgeEntityType,
  KnowledgeLinkType,
  NoteStatus,
  ResourceType,
} from '@/types/knowledge'

/** Shared badge chrome: icon, label, optional tooltip. Colour is never the
 *  only signal — every badge carries a word as well. */
function MetaBadge({
  meta,
  raw,
  size = 'default',
  className,
}: {
  meta: StatusMeta | undefined
  raw: string
  size?: 'sm' | 'default'
  className?: string
}) {
  // A value the vocabulary does not hold still renders: a row with no badge
  // reads as a missing badge, not as a value the server invented.
  const resolved = meta ?? { label: raw, icon: undefined, tone: 'neutral' as const }
  const Icon = resolved.icon
  const iconClass = size === 'sm' ? 'size-2.5' : 'size-3'

  return (
    <Badge
      variant={TONE_BADGE_VARIANT[resolved.tone]}
      className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}
      title={meta?.description}
    >
      {Icon ? <Icon aria-hidden="true" className={iconClass} /> : null}
      {resolved.label}
    </Badge>
  )
}

export interface KnowledgeBadgeProps {
  size?: 'sm' | 'default'
  className?: string
}

export function NoteStatusBadge({
  status,
  size,
  className,
}: KnowledgeBadgeProps & { status: NoteStatus }) {
  return (
    <MetaBadge
      meta={NOTE_STATUS_META[status]}
      raw={String(status)}
      size={size}
      className={className}
    />
  )
}

export function ResourceTypeBadge({
  type,
  size,
  className,
}: KnowledgeBadgeProps & { type: ResourceType }) {
  return (
    <MetaBadge
      meta={RESOURCE_TYPE_META[type]}
      raw={String(type)}
      size={size}
      className={className}
    />
  )
}

export function LinkTypeBadge({
  type,
  size,
  className,
}: KnowledgeBadgeProps & { type: KnowledgeLinkType }) {
  return (
    <MetaBadge
      meta={LINK_TYPE_META[type]}
      raw={String(type)}
      size={size}
      className={className}
    />
  )
}

export function EntityTypeBadge({
  type,
  size,
  className,
}: KnowledgeBadgeProps & { type: KnowledgeEntityType }) {
  return (
    <MetaBadge
      meta={KNOWLEDGE_ENTITY_META[type]}
      raw={String(type)}
      size={size}
      className={className}
    />
  )
}
