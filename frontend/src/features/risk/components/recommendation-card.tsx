import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { BadgeCheck, Check, CircleSlash, ExternalLink, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import {
  RecommendationStatusBadge,
  SeverityBadge,
} from '@/features/risk/components/severity-badge'
import { cn } from '@/lib/utils'
import { formatRelative } from '@/types/knowledge'
import type { RecommendationRead, UUIDString } from '@/types/risk'

/**
 * A proposed action, laid out as WHAT / WHY / what to do about it.
 *
 * **The three blocks are fixed and in this order because the brief requires all
 * three, and because the order is the argument.** `title` is WHAT the engine is
 * asking for, `reason` is WHY it thinks that with the numbers attached, and
 * `description` is the action spelled out. A reader who stops after `title` has
 * a bare imperative; a reader who reads the reason can disagree with it. Putting
 * the reason under the title rather than in a tooltip is deliberate — a tooltip
 * is unavailable to a keyboard user, hidden from a screen reader until focused,
 * and not present at all in a printed page.
 *
 * **`reason` is never elided and never replaced with a fallback.** The backend
 * makes an empty reason unconstructible, so anything shown here is a sentence
 * the service actually wrote. A `?? ''` here would restore exactly the bare
 * imperative the schema is designed to prevent.
 *
 * **The buttons are the lifecycle, not a sentiment scale.** Accept, reject and
 * complete are three different facts the backend records separately, and
 * "reject" is explicitly *not* a judgement on the underlying risk — the
 * suggestion can be raised again if the condition still holds. Reject is
 * therefore rendered as an ordinary ghost button and never as a destructive one:
 * styling it red would tell the user that declining a suggestion is a
 * destructive act, which it is not.
 *
 * Only the transitions that are legal from the current status are offered. The
 * server validates the from-state and answers a 409 otherwise, so a button that
 * is rendered-but-forbidden is a control that fails on click. `responded_at` is
 * shown once set, which is what makes "how many were never answered" readable
 * from the list without a second query.
 *
 * The priority chip reuses the severity chip: the backend derives one from the
 * other, so showing them with different chrome would imply they could disagree.
 */

const OPEN_STATUSES = new Set(['new', 'viewed'])
const ACCEPTED_STATUSES = new Set(['accepted'])

export interface RecommendationCardProps {
  recommendation: RecommendationRead
  onAccept?: (recommendation: RecommendationRead) => void
  onReject?: (recommendation: RecommendationRead) => void
  onComplete?: (recommendation: RecommendationRead) => void
  /** True while one of this card's transitions is in flight. */
  isActing?: boolean
  /** Message from the failed transition, shown under the buttons. */
  actionError?: string | null
  /** Where the risk behind this suggestion lives, when it has a page. */
  riskHref?: (recommendation: RecommendationRead) => string | null
  /** Opens the risk itself. Ignored when `riskHref` is supplied. */
  onOpenRisk?: (riskId: UUIDString) => void
  /** Heading level. `h3` inside a page section. */
  titleLevel?: 'h2' | 'h3' | 'h4'
  children?: ReactNode
  className?: string
}

export function RecommendationCard({
  recommendation,
  onAccept,
  onReject,
  onComplete,
  isActing = false,
  actionError = null,
  riskHref,
  onOpenRisk,
  titleLevel = 'h3',
  children,
  className,
}: RecommendationCardProps) {
  const status = recommendation.status
  const open = OPEN_STATUSES.has(status)
  const accepted = ACCEPTED_STATUSES.has(status)
  const href = riskHref ? riskHref(recommendation) : null
  const riskId = recommendation.risk_id

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-1.5">
            <CardTitle level={titleLevel} className="text-base leading-snug">
              {recommendation.title}
            </CardTitle>
            <p
              className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
            >
              {recommendation.recommendation_type.replace(/_/g, ' ')}
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1.5">
            <SeverityBadge severity={recommendation.priority} />
            <RecommendationStatusBadge status={status} size="sm" />
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <section className="space-y-1.5" aria-labelledby={`rec-${recommendation.id}-why`}>
          <h4
            id={`rec-${recommendation.id}-why`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Why
          </h4>
          {/* Rendered whole, in words, with the numbers the rule used. */}
          <p className="text-sm leading-relaxed text-foreground/90">{recommendation.reason}</p>
        </section>

        <section className="space-y-1.5" aria-labelledby={`rec-${recommendation.id}-action`}>
          <h4
            id={`rec-${recommendation.id}-action`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Suggested action
          </h4>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {recommendation.description}
          </p>
        </section>

        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span title={recommendation.created_at}>
            Raised {formatRelative(recommendation.created_at)}
          </span>
          {recommendation.responded_at && (
            <span title={recommendation.responded_at}>
              Answered {formatRelative(recommendation.responded_at)}
            </span>
          )}
          {recommendation.expires_at && (
            <span title={recommendation.expires_at}>
              Expired {formatRelative(recommendation.expires_at)}
            </span>
          )}
          {riskId &&
            (href ? (
              <Link
                to={href}
                className={cn(
                  'inline-flex items-center gap-1 font-medium text-foreground',
                  'underline-offset-2 hover:underline focus-visible:outline-none',
                  'focus-visible:ring-2 focus-visible:ring-ring',
                )}
              >
                View the finding
                <ExternalLink aria-hidden="true" className="size-3" />
              </Link>
            ) : onOpenRisk ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-auto px-0 text-xs"
                onClick={() => onOpenRisk(riskId)}
              >
                View the finding
              </Button>
            ) : null)}
        </p>

        {children}
      </CardContent>

      {open || accepted ? (
        <CardFooter className="flex flex-wrap items-center gap-2">
          {onAccept && open && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              aria-busy={isActing}
              disabled={isActing}
              onClick={() => onAccept(recommendation)}
            >
              {isActing ? <Spinner size="sm" label="" /> : <Check aria-hidden="true" />}
              Accept
            </Button>
          )}
          {onComplete && accepted && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              aria-busy={isActing}
              disabled={isActing}
              onClick={() => onComplete(recommendation)}
            >
              {isActing ? <Spinner size="sm" label="" /> : <BadgeCheck aria-hidden="true" />}
              Mark completed
            </Button>
          )}
          {onReject && open && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              aria-busy={isActing}
              disabled={isActing}
              onClick={() => onReject(recommendation)}
            >
              {isActing ? <Spinner size="sm" label="" /> : <X aria-hidden="true" />}
              Not for me
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
            <CircleSlash aria-hidden="true" className="size-3.5" />
            {status === 'rejected'
              ? 'Declined. The underlying condition may still be true, and the ' +
                'suggestion can be raised again if it still applies.'
              : 'No longer open. It stays on the record as something that was answered ' +
                'or made moot.'}
          </p>
        </CardFooter>
      )}
    </Card>
  )
}
