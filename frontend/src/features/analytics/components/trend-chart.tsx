import type { ReactNode } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChartShell, ChartTooltip } from '@/features/analytics/components/chart-shell'
import { CHART_AXIS_PROPS, type ChartValueUnit } from '@/features/analytics/chart-theme'
import { EmptyAnalytics, type AnalyticsMetricKey } from '@/features/analytics/components/empty-analytics'
import { chartColor } from '@/features/analytics/format'

export type ChartRow = Record<string, string | number | null | undefined>

export interface ChartSeries {
  /** Matches a key on each row. */
  key: string
  label: string
  /** Index into the five `--chart-*` tokens; assigned by position when omitted. */
  colorIndex?: number
  unit?: ChartValueUnit
}

/**
 * One chart component for every time series on this surface.
 *
 * A trend and an area chart differ by one prop, and six near-identical chart
 * files are six places for a shared axis or tooltip fix to be missed. The frame,
 * the tooltip, the axis typography and the empty state all come from
 * `chart-shell`, so this file is only the two recharts charts and the props
 * that make them legible.
 *
 * **Responsive, not scrollable.** The body is `min-w-0` inside the shell and
 * `ResponsiveContainer` measures the parent, so a long series compresses on a
 * phone rather than widening the page. That is why there is no horizontal
 * scroll escape hatch here: there is nothing to scroll.
 */
export interface TrendChartProps {
  title: string
  /** Usually the resolved window, so the chart says what it covers. */
  subtitle?: ReactNode
  data: readonly ChartRow[]
  series: readonly ChartSeries[]
  /** The row key the x axis reads. Defaults to `label`. */
  xKey?: string
  kind?: 'area' | 'line'
  actions?: ReactNode
  className?: string
  isLoading?: boolean
  /** Forces the empty state even when rows exist — e.g. a window with no work. */
  isEmpty?: boolean
  emptyMetric?: AnalyticsMetricKey
  emptyReason?: string | null
  /** How a dense x axis drops labels rather than rotating them into noise. */
  maxXTicks?: number
}

export function TrendChart({
  title,
  subtitle,
  data,
  series,
  xKey = 'label',
  kind = 'area',
  actions,
  className,
  isLoading = false,
  isEmpty = false,
  emptyMetric = 'trend',
  emptyReason,
  maxXTicks = 8,
}: TrendChartProps) {
  const units = Object.fromEntries(series.map((entry) => [entry.key, entry.unit ?? 'count']))
  const empty = isEmpty || data.length === 0

  // A dense window shows every `maxXTicks`-th label rather than all of them:
  // overlapping dates are less readable than a sampled axis.
  const tickInterval = data.length > maxXTicks ? Math.ceil(data.length / maxXTicks) - 1 : 0

  const body = (
    <ResponsiveContainer width="100%" height="100%">
      {kind === 'line' ? (
        <LineChart data={data as ChartRow[]} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
          <XAxis
            dataKey={xKey}
            interval={tickInterval}
            {...CHART_AXIS_PROPS}
          />
          <YAxis width={44} allowDecimals={false} {...CHART_AXIS_PROPS} />
          <Tooltip
            cursor={{ stroke: 'hsl(var(--border))' }}
            content={(props) => (
              <ChartTooltip
                active={props.active}
                label={props.label}
                payload={props.payload as never}
                units={units}
              />
            )}
          />
          {series.map((entry, index) => (
            <Line
              key={entry.key}
              type="monotone"
              dataKey={entry.key}
              name={entry.label}
              stroke={chartColor(entry.colorIndex ?? index)}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 3 }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      ) : (
        <AreaChart data={data as ChartRow[]} margin={{ top: 4, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="hsl(var(--border))" vertical={false} />
          <XAxis
            dataKey={xKey}
            interval={tickInterval}
            {...CHART_AXIS_PROPS}
          />
          <YAxis width={44} allowDecimals={false} {...CHART_AXIS_PROPS} />
          <Tooltip
            cursor={{ stroke: 'hsl(var(--border))' }}
            content={(props) => (
              <ChartTooltip
                active={props.active}
                label={props.label}
                payload={props.payload as never}
                units={units}
              />
            )}
          />
          {series.map((entry, index) => (
            <Area
              key={entry.key}
              type="monotone"
              dataKey={entry.key}
              name={entry.label}
              stroke={chartColor(entry.colorIndex ?? index)}
              fill={chartColor(entry.colorIndex ?? index)}
              fillOpacity={0.12}
              strokeWidth={2}
              isAnimationActive={false}
              // `connectNulls` stays off: the backend omits buckets with no
              // recorded activity, and drawing a straight line through one would
              // claim something happened on a day nothing was recorded.
              connectNulls={false}
            />
          ))}
        </AreaChart>
      )}
    </ResponsiveContainer>
  )

  return (
    <ChartShell
      title={title}
      subtitle={subtitle}
      actions={actions}
      isLoading={isLoading}
      className={className}
      empty={
        empty ? <EmptyAnalytics metric={emptyMetric} reason={emptyReason} /> : undefined
      }
    >
      {body}
    </ChartShell>
  )
}