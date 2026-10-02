import { formatMinutes, formatNumber } from '@/features/analytics/format'

/**
 * Axis, grid and tick styling, so every chart on this surface inherits one
 * scale of typography instead of each declaring its own.
 *
 * Recharts paints SVG *attributes*, not classes, so these are resolved token
 * values (`hsl(var(--border))`) rather than a `border-border` utility — which
 * is also why the colours are spelled the same way as `CHART_COLORS`.
 */
export const CHART_AXIS_PROPS = {
  stroke: 'hsl(var(--border))',
  tick: { fill: 'hsl(var(--muted-foreground))', fontSize: 11 },
  tickLine: false,
  axisLine: false,
} as const

/** How a series' values read inside a tooltip and an axis. */
export type ChartValueUnit = 'count' | 'minutes' | 'percent' | 'raw'

/**
 * `null` renders as "no value" here exactly as it does everywhere else on this
 * surface. A chart that plotted an absent bucket as `0` would be claiming the
 * day was measured and found empty.
 */
export function formatChartValue(value: number | null | undefined, unit: ChartValueUnit): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  if (unit === 'minutes') return formatMinutes(value)
  if (unit === 'percent') return `${formatNumber(value, 0)}%`
  return formatNumber(value, Number.isInteger(value) ? 0 : 1)
}
