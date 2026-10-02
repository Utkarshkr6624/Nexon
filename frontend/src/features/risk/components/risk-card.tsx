import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, Check, CircleSlash, Eye, Layers } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { formatSigned } from '@/features/analytics/format'
import { RiskScoreMeter } from '@/features/risk/components/risk-score-meter'
import { RiskStatusBadge, SeverityBadge } from '@/features/risk/components/severity-badge'
import { cn } from '@/lib/utils'
import { formatRelative } from '@/types/knowledge'
import type { RiskRead, UUIDString } from '@/types/risk'

/**
 * One detected risk, in full.
 *
 * **The card is ordered by what a reader needs in order to act on it:** what it
 * is, how loudly, how the number was reached, what it is about, when it was
 * found, and what to do. Anything else would put the "why" — the part that makes
 * the finding arguable — last, behind the buttons.
 *
 * **Every element the brief requires is present, and none of them is optional
 * to the reader.** Title, band, score, the evidence list under "Why", the
 * affected entity, the detected time and the suggested action each get their own
 * block. Where the data can honestly be absent the card says so in words rather
 * than leaving a gap: no suggestions is "No suggested action yet", which the
 * schema documents as a real state rather than a failure, and a terminal risk
 * replaces the action buttons with the timestamp at which it was closed.
 *
 * **The three buttons are three different answers, not three intensities.**
 * Acknowledge says "still true, I have seen it, stop asking"; resolve says "the
 * condition is over"; dismiss says "this does not apply to me". They are
 * deliberately not styled as primary/secondary/destructive, because none of them
 * is a destructive act on the user's data and a layout that made "Dismiss" look
 * like the dangerous one would be pushing them towards the wrong choice. Each is
 * rendered only when its handler is supplied, so a read-only placement of this
 * card cannot show a button that would do nothing.
 *
 * **The affected entity is named by kind, not by title.** `RiskRead` carries
 * `entity_type` and `entity_id` but no name, so the card says "Task" and lets
 * the page pass `entityHref` to make it a link. Inventing a name here would mean
 * either a second request per card or a fabricated label, and both are worse
 * than an honest kind.
 *
 * `ChartShell` is not used anywhere on this surface, deliberately: it fixes a
 * chart body to 256px and swaps in an empty state, and putting an
 * unbounded-height evidence list inside that frame would clip the one part of
 * the card that is not a fixed-height plot.
 */

const ENTITY_LABELS: Record<string, string> = {
  task: 'Task',
  project: 'Project',
  account: 'Account',
}

/** Entity kinds are open — the card shows the server's own word when it is new. */
function entityLabel(entityType: string): string {
  return (
    ENTITY_LABELS[entityType] ??
    entityType.charAt(0).toUpperCase() + entityType.slice(1).replace(/_/g, ' ')
  )
}

/**
 * What "evidence strength" is allowed to say.
 *
 * The types are explicit that this is *not* confidence and must never be
 * rendered as a percentage: it reports how many observations the rule had, and
 * the backend's own thresholds are 30 samples for high and 10 for medium. The
 * copy therefore describes the volume in words and never converts it into a
 * figure, because a "confidence: 73%" would be a claim about a probability the
 * engine never computed.
 */
const STRENGTH_COPY: Record<RiskRead['evidence_strength'], string> = {
  high: 'Drawn from a large number of recorded inputs.',
  medium: 'Drawn from a moderate number of recorded inputs.',
  low: 'Drawn from a small number of recorded inputs. The finding may change as more is recorded.',
}

export interface RiskCardProps {
  risk: RiskRead
  onAcknowledge?: (risk: RiskRead) => void
  onDismiss?: (risk: RiskRead) => void
  onResolve?: (risk: RiskRead) => void
  /** True while one of this card's transitions is in flight. */
  isActing?: boolean
  /** Message from the failed transition, shown under the buttons. */
  actionError?: string | null
  /** Where the affected entity lives, when it has a page. */
  entityHref?: (risk: RiskRead) => string | null
  /** Opens the full recommendation. Absent on a Risk Center list row. */
  onOpenRecommendation?: (id: UUIDString) => void
  /** Heading level. `h3` inside a page section; pass `h2` when the card is the
   *  only thing on the screen. */
  titleLevel?: 'h2' | 'h3' | 'h4'
  children?: ReactNode
  className?: string
}

export function RiskCard({
  risk,
  onAcknowledge,
  onDismiss,
  onResolve,
  isActing = false,
  actionError = null,
  entityHref,
  onOpenRecommendation,
  titleLevel = 'h3',
  children,
  className,
}: RiskCardProps) {
  const live = risk.status === 'active' || risk.status === 'acknowledged'
  const href = entityHref ? entityHref(risk) : null
  const entityText = risk.entity_type ? entityLabel(risk.entity_type) : 'The account as a whole'

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1.5">
            <CardTitle level={titleLevel} className="text-base leading-snug">
              {risk.title}
            </CardTitle>
            <p className="text-sm leading-relaxed text-muted-foreground">{risk.description}</p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            <SeverityBadge severity={risk.severity} />
            <RiskStatusBadge status={risk.status} size="sm" />
          </div>
        </div>

        <RiskScoreMeter score={risk.score} severity={risk.severity} size="sm" />

        <p className="text-xs text-muted-foreground">
          {href ? (
            <Link
              to={href}
              className="font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {entityText}
            </Link>
          ) : (
            <span className="font-medium text-foreground">{entityText}</span>
          )}
          {risk.entity_type ? '' : ' — no single task or project is named by this finding.'}
        </p>
      </CardHeader>

      <CardContent className="space-y-4">
        <section className="space-y-2" aria-labelledby={`risk-${risk.id}-why`}>
          <h4
            id={`risk-${risk.id}-why`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Why
          </h4>

          {risk.evidence.length > 0 ? (
            <ul className="space-y-2">
              {risk.evidence.map((item, index) => (
                <li
                  key={`${item.label}-${index}`}
                  className="rounded-md border border-border bg-muted/40 px-3 py-2"
                >
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="min-w-0 text-sm font-medium text-foreground">{item.label}</p>
                    <p
                      className="shrink-0 text-xs tabular-nums text-muted-foreground"
                      title="How much this input moved the score"
                    >
                      {formatSigned(item.contribution, 1)}
                    </p>
                  </div>
                  <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{item.detail}</p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm leading-relaxed text-muted-foreground">
              This finding carries no recorded evidence lines.
            </p>
          )}

          <p className="text-xs text-muted-foreground">
            Evidence strength: {risk.evidence_strength}. {STRENGTH_COPY[risk.evidence_strength]}
          </p>
        </section>

        <section className="space-y-1.5" aria-labelledby={`risk-${risk.id}-action`}>
          <h4
            id={`risk-${risk.id}-action`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Suggested action
          </h4>

          {risk.recommendations.length > 0 ? (
            <ul className="space-y-2">
              {risk.recommendations.map((suggestion) => (
                <li key={suggestion.id} className="rounded-md border border-border px-3 py-2">
                  <p className="text-sm font-medium text-foreground">{suggestion.title}</p>
                  {/* The projection drops `description`, so `reason` is the only
                      sentence available here — which is exactly why the types
                      keep it on the nested shape. */}
                  <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                    {suggestion.reason}
                  </p>
                  {onOpenRecommendation && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="mt-1 h-7 px-2 text-xs"
                      onClick={() => onOpenRecommendation(suggestion.id)}
                    >
                      Open suggestion
                      <ArrowRight aria-hidden="true" />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm leading-relaxed text-muted-foreground">
              No suggested action yet. Every rule that could propose one either had nothing to
              propose for this condition, or a suggestion already exists outside this risk.
            </p>
          )}
        </section>

        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span title={risk.detected_at}>Detected {formatRelative(risk.detected_at)}</span>
          {risk.resolved_at && <span title={risk.resolved_at}>Closed {formatRelative(risk.resolved_at)}</span>}
          <span className="capitalize">{risk.risk_type} check</span>
        </p>

        {children}
      </CardContent>

      {live ? (
        <CardFooter className="flex flex-wrap items-center gap-2">
          {onAcknowledge && risk.status === 'active' && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={isActing}
              onClick={() => onAcknowledge(risk)}
            >
              {isActing ? <Spinner size="sm" /> : <Eye aria-hidden="true" />}
              Acknowledge
            </Button>
          )}
          {onResolve && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={isActing}
              onClick={() => onResolve(risk)}
            >
              {isActing ? <Spinner size="sm" /> : <Check aria-hidden="true" />}
              Mark resolved
            </Button>
          )}
          {onDismiss && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={isActing}
              onClick={() => onDismiss(risk)}
            >
              {isActing ? <Spinner size="sm" /> : <CircleSlash aria-hidden="true" />}
              Dismiss
            </Button>
          )}

          {actionError && (
            <p role="alert" className="w-full text-xs leading-relaxed text-destructive">
              {actionError}
            </p>
          )}
        </CardFooter>
      ) : (
        <CardFooter>
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Layers aria-hidden="true" className="size-3.5" />
            Closed. {risk.status === 'dismissed' ? 'Dismissed' : 'Resolved'}
            {risk.resolved_at ? ` ${formatRelative(risk.resolved_at)}` : ''}. It is no longer part
            of the live list.
          </p>
        </CardFooter>
      )}
    </Card>
  )
}

export interface RiskEntityLinkProps {
  risk: RiskRead
  href: string
  className?: string
}

/**
 * The affected entity as a link, for a page that already knows the route shape.
 *
 * Kept separate from the card so a list row and a detail header can place it
 * differently while sharing one label, and so the card itself never has to know
 * a route.
 */
export function RiskEntityLink({ risk, href, className }: RiskEntityLinkProps) {
  if (!risk.entity_type) return null
  return (
    <Link
      to={href}
      className={cn(
        'text-xs font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className,
      )}
    >
      {entityLabel(risk.entity_type)}
    </Link>
  )
}
