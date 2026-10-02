/**
 * The risk surface's vocabulary: what each band, status and priority is called,
 * which icon carries it, which tone it wears and one sentence explaining it.
 *
 * **This is a `.tsx` file with no JSX in it, and that is deliberate.** The
 * project runs `react-refresh/only-export-components`, which reports any file
 * that exports a component *and* a non-component. `allowConstantExport` is on,
 * but the plugin's implementation only exempts literals, template literals,
 * unary and binary expressions — an object of metadata is not on that list, so
 * the maps cannot sit beside the badge components that consume them. The rule's
 * own error message gives the remedy: "use a new file to share constants". That
 * file belongs under `components/` because these tables describe how this
 * surface renders its vocabulary, and putting them in a `.ts` file would mean
 * either reaching outside the feature's own tree for pure presentation data or
 * editing `src/types/risk.ts`, which mirrors the backend and is not this layer's
 * to own.
 *
 * **The bands are described by the thresholds the backend actually uses.**
 * `DEFAULT_SEVERITY_THRESHOLDS` in `app/services/risk/scoring.py` floors them at
 * 75 / 50 / 25, so the descriptions state those numbers rather than an
 * impression. A reader who wants to know why a row landed in a band can check
 * the score on the meter against the sentence here, and the two cannot drift
 * apart without someone editing a number on purpose.
 *
 * The shapes are chosen so the four bands differ in silhouette as well as hue —
 * `OctagonAlert` → `TriangleAlert` → `CircleAlert` → `ShieldCheck` is a ladder a
 * reader can order with the page desaturated. That is the whole reason the
 * brief's "never colour alone" rule is discharged by this table rather than by
 * a comment.
 */

import {
  BadgeCheck,
  Ban,
  CircleAlert,
  CircleCheck,
  CircleDot,
  Clock,
  Eye,
  OctagonAlert,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
  X,
} from 'lucide-react'

import type {
  RecommendationStatus,
  RiskSeverity,
  RiskStatus,
} from '@/types/risk'
import type { StatusMeta } from '@/types/work'

/** The four severity bands, most severe first — the order the backend sorts in. */
export const SEVERITY_META: Record<RiskSeverity, StatusMeta> = {
  critical: {
    label: 'Critical',
    icon: OctagonAlert,
    tone: 'danger',
    description: 'A score of 75 or above out of 100.',
  },
  high: {
    label: 'High',
    icon: TriangleAlert,
    tone: 'warning',
    description: 'A score from 50 to 74 out of 100.',
  },
  medium: {
    label: 'Medium',
    icon: CircleAlert,
    tone: 'info',
    description: 'A score from 25 to 49 out of 100.',
  },
  low: {
    label: 'Low',
    icon: ShieldCheck,
    tone: 'neutral',
    description: 'A score below 25 out of 100.',
  },
}

/**
 * Where a risk sits in its lifecycle. The two live states share a tone on
 * purpose — `acknowledged` is not calmer than `active`, only quieter — and the
 * two terminal states share `success`, because a closed risk is a completed
 * question rather than a failure, whatever the answer was.
 */
export const RISK_STATUS_META: Record<RiskStatus, StatusMeta> = {
  active: {
    label: 'Active',
    icon: CircleDot,
    tone: 'info',
    description: 'Detected and not yet answered.',
  },
  acknowledged: {
    label: 'Acknowledged',
    icon: Eye,
    tone: 'info',
    description: 'Seen and still true. It keeps being re-checked but no longer asks for attention.',
  },
  resolved: {
    label: 'Resolved',
    icon: CircleCheck,
    tone: 'success',
    description: 'The condition behind it is no longer present.',
  },
  dismissed: {
    label: 'Dismissed',
    icon: Ban,
    tone: 'neutral',
    description: 'Closed as not applying.',
  },
}

/**
 * Where a suggestion sits. `expired` is a service decision rather than a clock:
 * the risk behind the suggestion was resolved, so the suggestion became moot,
 * and recording that is more useful than recording that it got old.
 */
export const RECOMMENDATION_STATUS_META: Record<RecommendationStatus, StatusMeta> = {
  new: {
    label: 'New',
    icon: Sparkles,
    tone: 'info',
    description: 'Raised and not yet read.',
  },
  viewed: {
    label: 'Viewed',
    icon: Eye,
    tone: 'info',
    description: 'Read and not yet answered.',
  },
  accepted: {
    label: 'Accepted',
    icon: CircleCheck,
    tone: 'success',
    description: 'Taken on as work to do.',
  },
  rejected: {
    label: 'Rejected',
    icon: X,
    tone: 'neutral',
    description: 'Declined. The underlying condition may still be true.',
  },
  completed: {
    label: 'Completed',
    icon: BadgeCheck,
    tone: 'success',
    description: 'Acted on.',
  },
  expired: {
    label: 'Expired',
    icon: Clock,
    tone: 'neutral',
    description: 'The risk behind it was resolved, so it no longer applies.',
  },
}
