import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { PRIORITY_META } from '@/types/work'
import type { ProjectPriority, TaskPriority } from '@/types/work'

import { TONE_BADGE_VARIANT } from './badge-tone'

export interface PriorityBadgeProps {
  priority: ProjectPriority | TaskPriority
  /** `sm` drops the padding for dense rows and board cards. */
  size?: 'sm' | 'default'
  className?: string
}

/** The grade, not the project it belongs to — projects and tasks share the scale. */
export function PriorityBadge({ priority, size = 'default', className }: PriorityBadgeProps) {
  const meta = PRIORITY_META[priority] ?? PRIORITY_META.medium
  const Icon = meta.icon

  return (
    <Badge
      variant={TONE_BADGE_VARIANT[meta.tone]}
      className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}
      title={meta.description}
    >
      <Icon aria-hidden="true" className={size === 'sm' ? 'size-2.5' : 'size-3'} />
      {meta.label}
    </Badge>
  )
}