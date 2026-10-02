import type { StatusMeta } from '@/types/work'

/**
 * `StatusMeta.tone` names a token family rather than a colour, and this is the
 * only place that maps onto a `Badge` variant — so no component ever reaches
 * for a raw hex value, and the two badge families cannot drift apart.
 */
export const TONE_BADGE_VARIANT = {
  neutral: 'secondary',
  info: 'default',
  success: 'success',
  warning: 'warning',
  danger: 'destructive',
} as const satisfies Record<
  StatusMeta['tone'],
  'secondary' | 'default' | 'success' | 'warning' | 'destructive'
>