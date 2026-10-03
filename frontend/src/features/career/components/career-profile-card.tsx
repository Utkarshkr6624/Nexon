import type { ReactNode } from 'react'
import { ExternalLink, Globe, MapPin, Quote, Target, UserRound } from 'lucide-react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  CareerEmptyState,
  CareerRegionError,
  CareerStaleNotice,
} from '@/features/career/components/career-empty-state'
import {
  describeLinkLabel,
  formatCareerInstant,
  isNavigableLink,
} from '@/features/career/components/career-format'
import { cn } from '@/lib/utils'
import type { ApiError } from '@/lib/api-client'
import type { CareerProfileRead } from '@/types/learning'

/**
 * The career profile, as the user filled it in.
 *
 * ## Every word on this card was typed by the person it describes
 *
 * There is no generated headline, no inferred target role and no summarised
 * paragraph anywhere on this surface. That is not a stylistic preference: a
 * career profile is a claim a person makes to someone else, and anything NEXUS
 * wrote into it would be a claim it has no standing to make. Every field is
 * therefore rendered either as the user's own words or as a sentence saying it
 * is empty — never as a fill-in.
 *
 * ## The nulls are the common case, and each one gets its own sentence
 *
 * A profile with a headline and nothing else is a perfectly ordinary profile. So:
 * no headline falls back to the target role as the card's title **and says the
 * headline is not set**; a missing target role says the role has not been
 * chosen; empty links say links appear when one is added, not "no links"; and a
 * profile with no summary says the summary is theirs to write. Collapsing these
 * into one "incomplete profile" banner would tell a reader something is wrong
 * with a page that is doing exactly what they asked of it.
 *
 * ## A link is only an anchor when it is a URL
 *
 * `links` is a plain `string[]` of whatever the user pasted, so a value that
 * does not parse is rendered as **plain text** and not as an anchor — an
 * `<a href>` built from a string that is not a URL is either dead or, worse,
 * navigable somewhere the user did not intend.
 */
export interface CareerProfileCardProps {
  profile: CareerProfileRead
  titleLevel?: 'h3' | 'h4'
  /** The page's own actions, rendered under the body. */
  actions?: ReactNode
  className?: string
}

export function CareerProfileCard({
  profile,
  titleLevel = 'h3',
  actions,
  className,
}: CareerProfileCardProps) {
  const heading = profile.headline?.trim() || profile.target_role?.trim() || 'Your career profile'

  return (
    <Card className={cn('min-w-0', className)}>
      <CardHeader className="space-y-3">
        <div className="min-w-0 space-y-1">
          <CardTitle level={titleLevel} className="line-clamp-2 text-base leading-snug">
            {heading}
          </CardTitle>
          {profile.headline && profile.target_role && (
            <p className="flex min-w-0 items-center gap-1.5 text-sm text-muted-foreground">
              <Target aria-hidden="true" className="size-3.5 shrink-0" />
              <span className="min-w-0 truncate">{profile.target_role}</span>
            </p>
          )}
        </div>

        <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className="flex min-w-0 items-center gap-1.5">
            <Globe aria-hidden="true" className="size-3 shrink-0" />
            {profile.target_domain ? (
              <span className="truncate">Domain: {profile.target_domain}</span>
            ) : (
              <span>No target domain set</span>
            )}
          </span>
          <span className="flex min-w-0 items-center gap-1.5">
            <MapPin aria-hidden="true" className="size-3 shrink-0" />
            {profile.location ? (
              <span className="truncate">{profile.location}</span>
            ) : (
              <span>No location set</span>
            )}
          </span>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <section className="space-y-1.5" aria-labelledby={`profile-${profile.id}-about`}>
          <h4
            id={`profile-${profile.id}-about`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            In your words
          </h4>
          {profile.summary ? (
            <p className="flex min-w-0 gap-2 text-sm leading-relaxed text-foreground/90">
              <Quote aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 whitespace-pre-line">{profile.summary}</span>
            </p>
          ) : (
            <p className="text-sm leading-relaxed text-muted-foreground">
              No summary written yet. This paragraph is yours — NEXUS does not draft one, because
              a summary it wrote would be a claim it has no standing to make on your behalf.
            </p>
          )}
        </section>

        <section className="space-y-1.5" aria-labelledby={`profile-${profile.id}-links`}>
          <h4
            id={`profile-${profile.id}-links`}
            className="text-[11px] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
          >
            Links
          </h4>
          {profile.links.length === 0 ? (
            <p className="text-sm leading-relaxed text-muted-foreground">
              No links yet. Anything you add — a repository, a portfolio, a profile page — is
              listed here exactly as you entered it.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {profile.links.map((link, index) => (
                <li key={`${link}-${index}`} className="flex min-w-0 items-center gap-1.5 text-sm">
                  <ExternalLink aria-hidden="true" className="size-3.5 shrink-0 text-muted-foreground" />
                  {isNavigableLink(link) ? (
                    <a
                      href={link}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="min-w-0 truncate font-medium text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {describeLinkLabel(link)}
                    </a>
                  ) : (
                    <span
                      className="min-w-0 truncate text-muted-foreground"
                      title="This link was not recognised as a web address, so it is shown as the text you entered."
                    >
                      {link}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border pt-3 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <UserRound aria-hidden="true" className="size-3 shrink-0" />
            Everything above was entered by you.
          </span>
          <span aria-hidden="true">·</span>
          <span>Last edited {formatCareerInstant(profile.updated_at)}</span>
        </p>

        {actions && (
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        )}
      </CardContent>
    </Card>
  )
}

/* ------------------------------------------------------------------ skeleton */

/**
 * The profile card's silhouette.
 *
 * **No digits and no placeholder link rows with text.** A profile is entirely
 * user-supplied text, so a skeleton that rendered a fake headline or a fake URL
 * would put invented words — the one thing this surface may not do — on screen
 * while the real ones load. The blocks are shape only.
 */
export function CareerProfileCardSkeleton({ className }: { className?: string }) {
  return (
    <Card className={cn('min-w-0', className)} aria-hidden="true">
      <CardHeader className="space-y-3">
        <div className="min-w-0 space-y-2">
          <Skeleton className="h-4 w-3/5" />
          <Skeleton className="h-3 w-2/5" />
        </div>
        <div className="flex gap-4">
          <Skeleton className="h-3 w-28" />
          <Skeleton className="h-3 w-24" />
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {[0, 1].map((section) => (
          <div key={section} className="space-y-2">
            <Skeleton className="h-2.5 w-28" />
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-4/5" />
          </div>
        ))}
        <Skeleton className="h-3 w-1/2" />
      </CardContent>
    </Card>
  )
}

export interface CareerProfileRegionProps {
  /** Null until `PUT /career/profile` has been called for this account. */
  profile: CareerProfileRead | null
  isLoading?: boolean
  isStale?: boolean
  error?: ApiError | null
  onRetry?: () => void
  /** Replaces the empty copy — the backend's own reason, verbatim. */
  emptyReason?: string | null
  emptyAction?: ReactNode
  titleLevel?: 'h3' | 'h4'
  actions?: ReactNode
  className?: string
}

/**
 * The profile region, with its loading, empty, error and stale states.
 *
 * **`profile: null` is a real state, not a failure.** A profile that has never
 * been written is a 404 on the read and the target of the upsert, so it gets the
 * cold-start empty state — which names the fields the user fills in — rather than
 * an error or a blank card.
 */
export function CareerProfileRegion({
  profile,
  isLoading = false,
  isStale = false,
  error = null,
  onRetry,
  emptyReason = null,
  emptyAction,
  titleLevel = 'h3',
  actions,
  className,
}: CareerProfileRegionProps) {
  if (isLoading) return <CareerProfileCardSkeleton className={className} />

  if (error) {
    return (
      <div className={cn('rounded-lg border border-border bg-card p-4', className)}>
        <CareerRegionError error={error} onRetry={onRetry} subject="the career profile" />
      </div>
    )
  }

  if (!profile) {
    return (
      <CareerEmptyState
        variant="profile"
        reason={emptyReason}
        action={emptyAction}
        className={cn('rounded-lg border border-border bg-card', 'min-h-[16rem]', className)}
      />
    )
  }

  return (
    <div className={cn('space-y-3', className)}>
      <CareerStaleNotice isStale={isStale} subject="the career profile" />
      <CareerProfileCard profile={profile} titleLevel={titleLevel} actions={actions} />
    </div>
  )
}