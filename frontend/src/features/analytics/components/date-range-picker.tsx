import { useId, useState } from 'react'
import { CalendarRange } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { formatRangeLabel } from '@/types/analytics'
import { WINDOW_PRESETS, type DateOnlyString, type Granularity, type WindowPresetId } from '@/types/analytics'
import { cn } from '@/lib/utils'

const GRANULARITIES: readonly Granularity[] = ['day', 'week', 'month']

/**
 * The analytics window control, and the date range on the page with it.
 *
 * **The window lives in the URL**, so `?range=30d&tab=projects` is a view: the
 * person it is sent to sees the same thirty days, and the back button walks
 * through windows rather than out of the page. This component owns no state of
 * its own beyond the unapplied custom inputs — it is handed the resolved window
 * and reports a choice back, which is why the dashboard and the Analytics page
 * can share one control and one URL contract.
 *
 * The custom inputs are held locally until **Apply** because a range picker that
 * fires on every keystroke would issue a request per character; the preset
 * buttons apply immediately, since they need no editing.
 */
export interface DateRangePickerProps {
  preset: WindowPresetId
  start: DateOnlyString
  end: DateOnlyString
  onPresetChange: (preset: WindowPresetId) => void
  onCustomChange: (start: DateOnlyString, end: DateOnlyString) => void
  /** Adds the day/week/month bucket switch; only meaningful for series. */
  granularity?: Granularity
  onGranularityChange?: (granularity: Granularity) => void
  className?: string
}

export function DateRangePicker({
  preset,
  start,
  end,
  onPresetChange,
  onCustomChange,
  granularity,
  onGranularityChange,
  className,
}: DateRangePickerProps) {
  const [customOpen, setCustomOpen] = useState(preset === 'custom')
  const startId = useId()
  const endId = useId()

  const isCustom = preset === 'custom'

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
          <CalendarRange className="size-3.5" aria-hidden="true" />
          Window
        </span>

        <div
          role="group"
          aria-label="Date range"
          className="flex flex-wrap items-center gap-1"
        >
          {WINDOW_PRESETS.map((option) => {
            const selected = option.id === preset
            return (
              <Button
                key={option.id}
                type="button"
                size="sm"
                variant={selected ? 'secondary' : 'ghost'}
                aria-pressed={selected}
                onClick={() => {
                  if (option.id === 'custom') setCustomOpen(true)
                  onPresetChange(option.id)
                }}
              >
                {option.label}
              </Button>
            )
          })}
        </div>

        {onGranularityChange && granularity && (
          <div className="flex items-center gap-2">
            <Label htmlFor={`${startId}-granularity`} className="text-xs text-muted-foreground">
              Bucket
            </Label>
            <select
              id={`${startId}-granularity`}
              value={granularity}
              onChange={(event) => onGranularityChange(event.target.value as Granularity)}
              className="h-8 rounded-md border border-input bg-background px-2 text-xs"
            >
              {GRANULARITIES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {customOpen ? (
        /* Keyed on the resolved window, so arriving at Custom from the back
           button — or from a link — remounts the fields with the dates on
           screen. An effect that copied props into state would be the same
           intent with an extra render and a stale-value window. */
        <CustomRangeFields
          key={`${isCustom}-${start}-${end}`}
          start={start}
          end={end}
          startId={startId}
          endId={endId}
          applied={isCustom}
          onApply={onCustomChange}
        />
      ) : (
        <p className="text-xs text-muted-foreground">{formatRangeLabel(start, end)}</p>
      )}
    </div>
  )
}

interface CustomRangeFieldsProps {
  start: DateOnlyString
  end: DateOnlyString
  startId: string
  endId: string
  /** True once the custom window has been applied, so the caption can show it. */
  applied: boolean
  onApply: (start: DateOnlyString, end: DateOnlyString) => void
}

/**
 * The two date inputs, held locally until Apply.
 *
 * Separate from the picker so its draft state is initialised from props once,
 * on mount, rather than being copied in afterwards: the parent keys this
 * component on the window, and a remount is how new values arrive.
 */
function CustomRangeFields({
  start,
  end,
  startId,
  endId,
  applied,
  onApply,
}: CustomRangeFieldsProps) {
  const [draftStart, setDraftStart] = useState(start)
  const [draftEnd, setDraftEnd] = useState(end)

  return (
    <div className="flex flex-wrap items-end gap-3 rounded-md border border-border bg-muted/40 p-3">
      <div className="space-y-1">
        <Label htmlFor={startId} className="text-xs text-muted-foreground">
          From
        </Label>
        <Input
          id={startId}
          type="date"
          value={draftStart}
          max={draftEnd}
          onChange={(event) => setDraftStart(event.target.value)}
          className="h-8 w-40 text-xs"
        />
      </div>
      <div className="space-y-1">
        <Label htmlFor={endId} className="text-xs text-muted-foreground">
          To
        </Label>
        <Input
          id={endId}
          type="date"
          value={draftEnd}
          min={draftStart}
          onChange={(event) => setDraftEnd(event.target.value)}
          className="h-8 w-40 text-xs"
        />
      </div>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={!draftStart || !draftEnd || draftStart > draftEnd}
        onClick={() => onApply(draftStart, draftEnd)}
      >
        Apply range
      </Button>
      <p className="text-xs text-muted-foreground">
        {applied ? formatRangeLabel(start, end) : 'Pick the two ends, then apply.'}
      </p>
    </div>
  )
}
