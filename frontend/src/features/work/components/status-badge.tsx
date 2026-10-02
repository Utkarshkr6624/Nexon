import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { PROJECT_STATUS_META, TASK_STATUS_META } from '@/types/work'
import type { ProjectStatus, TaskStatus } from '@/types/work'

import { TONE_BADGE_VARIANT } from './badge-tone'

export interface StatusBadgeProps {
  status: ProjectStatus | TaskStatus
  /** Which vocabulary `status` is drawn from. Projects and tasks both have one. */
  kind?: 'task' | 'project'
  size?: 'sm' | 'default'
  className?: string
}

export function StatusBadge({
  status,
  kind = 'task',
  size = 'default',
  className,
}: StatusBadgeProps) {
  const meta =
    kind === 'project'
      ? PROJECT_STATUS_META[status as ProjectStatus]
      : TASK_STATUS_META[status as TaskStatus]

  // An unknown value must still render as something: a row with no badge reads
  // as a bug in the badge rather than a value the vocabulary does not hold.
  const resolved = meta ?? { label: String(status), icon: undefined, tone: 'neutral' as const }
  const Icon = resolved.icon

  return (
    <Badge
      variant={TONE_BADGE_VARIANT[resolved.tone]}
      className={cn('gap-1', size === 'sm' && 'px-1.5 py-0 text-[10px]', className)}
      title={resolved.description}
    >
      {Icon ? <Icon aria-hidden="true" className={size === 'sm' ? 'size-2.5' : 'size-3'} /> : null}
      {resolved.label}
    </Badge>
  )
}