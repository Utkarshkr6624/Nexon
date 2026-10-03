/**
 * The learning surface's vocabulary: what each goal status, priority, activity
 * type and level source is called, which icon carries it, which tone it wears
 * and one sentence saying what it means.
 *
 * **This is a `.tsx` file with no JSX in it, and that is deliberate.** The
 * project runs `react-refresh/only-export-components`, which reports a file that
 * exports a component *and* a non-component; `allowConstantExport` is on but the
 * plugin only exempts literals, so an object of metadata cannot sit beside the
 * badges that consume it. The rule's own error message gives the remedy — "use a
 * new file to share constants" — and this file belongs under `components/`
 * because these tables describe how this surface renders its vocabulary.
 *
 * ## Nothing here measures a person
 *
 * Every label names **a record or a claim**, never a verdict. There is no
 * "strength", no "weakness", no "confidence in the person" anywhere in this
 * file: `self-assessed` describes *who typed the number*, `NEXUS estimate`
 * describes *who derived it and from what*, and the activity types name the kind
 * of event that was recorded. `task_completed` in particular is the name of an
 * **event type**, and a screen that rendered it as "you completed a task" would
 * be claiming an outcome the record does not carry — the record says a task was
 * marked complete, which is a different claim.
 *
 * ## The tones are about the record's state, not its worth
 *
 * `completed` wears `success` because the *goal* is finished, and `archived`
 * wears `neutral` because an archived goal is not outstanding work — it is filed
 * away. Neither colour says the goal was a good idea, and no level or gap in
 * this file carries a tone at all: a level is the user's or a labelled
 * estimate, and colouring 3/5 as better than 2/5 would be a comparison NEXUS has
 * no standing to make.
 */

import {
  Archive,
  ArrowDown,
  ArrowUp,
  BookMarked,
  ChevronsUp,
  CircleCheck,
  CircleDashed,
  CircleDot,
  ClipboardCheck,
  Eye,
  FileCode2,
  FolderCheck,
  Lightbulb,
  Minus,
  MousePointerClick,
  PauseCircle,
  Sigma,
  UserRound,
  type LucideIcon,
} from 'lucide-react'

import {
  INSUFFICIENT_DATA_MESSAGE,
  MAX_SKILL_LEVEL,
  type LearningActivityType,
  type LearningGoalStatus,
  type SkillLevelSource,
} from '@/types/learning'
import type { ProjectPriority, StatusMeta } from '@/types/work'

/* ------------------------------------------------------------- the level scale */

/**
 * The top of the 1–5 scale the backend constrains `current_level` and
 * `target_level` with.
 *
 * Stated as a literal with a `satisfies` tie back to `MAX_SKILL_LEVEL`, rather
 * than as a reference to it: a plain re-export would initialise from an
 * identifier, and `react-refresh/only-export-components` reads an
 * upper-case-initialised identifier export as a React component — which turns
 * this file into one it is not. The `satisfies` clause fails to compile the
 * moment the two disagree, so the single source of truth is still the wire
 * types.
 */
export const LEARNING_LEVEL_SCALE = 5 satisfies typeof MAX_SKILL_LEVEL

/**
 * The title every insufficient-data state on this surface reads.
 *
 * One sentence, one meaning: the measurement could not be made. It is **not** a
 * zero, and a card carrying it must not also print a figure — the backend's own
 * `reason_if_unavailable` goes underneath it, verbatim, because it names the
 * specific ingredient that was missing and a generic sentence cannot.
 *
 * Restated as a literal rather than referenced from `INSUFFICIENT_DATA_MESSAGE`,
 * for the same reason the developer surface restates its own: initialising an
 * upper-case export from an upper-case *identifier* makes
 * `react-refresh/only-export-components` read this file as one that exports a
 * React component. The annotation below is a compile-time tie to the wire
 * constant, so the two cannot drift: widen the type and this file stops
 * compiling.
 */
export const NOT_ENOUGH_DATA_TITLE: typeof INSUFFICIENT_DATA_MESSAGE = 'Not enough data yet.'

/* ---------------------------------------------------------------- goal status */

/**
 * Where a goal sits in its own life.
 *
 * `archived` is deliberately a different shape from `completed` even though both
 * are terminal: an archived goal is not outstanding work and must not be counted
 * as something left to do, which is why its label is "Archived" and not
 * "Completed" in a quieter tone. Every description says whose claim the status
 * is — it is the user's own field.
 */
export const GOAL_STATUS_META: Record<LearningGoalStatus, StatusMeta> = {
  not_started: {
    label: 'Not started',
    icon: CircleDashed,
    tone: 'neutral',
    description:
      'The goal is written down and nothing has been recorded against it yet. Its progress ' +
      'figure is 0, which is a measurement, not a missing value.',
  },
  in_progress: {
    label: 'In progress',
    icon: CircleDot,
    tone: 'info',
    description:
      'You have marked this goal as under way. The progress percentage beside it is the one ' +
      'you set — NEXUS does not derive it from activity.',
  },
  paused: {
    label: 'Paused',
    icon: PauseCircle,
    tone: 'warning',
    description:
      'Set aside for now and still counted as open. Pausing records a decision you made; it ' +
      'says nothing about whether the goal was worth doing.',
  },
  completed: {
    label: 'Completed',
    icon: CircleCheck,
    tone: 'success',
    description:
      'The goal was completed, and the date it was completed on is recorded with it. This is a ' +
      'fact about the record, not a judgement about the work.',
  },
  archived: {
    label: 'Archived',
    icon: Archive,
    tone: 'neutral',
    description:
      'Finished with and filed away. An archived goal is not outstanding work and is not counted ' +
      'among the goals still open.',
  },
}

/**
 * How urgent the user said this goal is.
 *
 * `LearningGoal.priority` reuses the existing `ProjectPriority` vocabulary
 * rather than inventing a near-identical second one, so a goal and a project
 * cannot describe the same urgency in two disagreeing scales. The descriptions
 * state plainly that this is an ordering the user chose.
 */
export const GOAL_PRIORITY_META: Record<ProjectPriority, StatusMeta> = {
  low: {
    label: 'Low priority',
    icon: ArrowDown,
    tone: 'neutral',
    description: 'An ordering you gave this goal. It is not a statement about its importance.',
  },
  medium: {
    label: 'Medium priority',
    icon: Minus,
    tone: 'neutral',
    description: 'The middle of the ordering you gave this goal.',
  },
  high: {
    label: 'High priority',
    icon: ArrowUp,
    tone: 'info',
    description: 'Ordered above the medium goals in the list you are looking at.',
  },
  critical: {
    label: 'Critical priority',
    icon: ChevronsUp,
    tone: 'warning',
    description:
      'The top of the ordering you gave this goal. It means "deal with this first", not that ' +
      'anything is wrong.',
  },
}

/* ------------------------------------------------------------- activity types */

/**
 * What kind of event was recorded as evidence that something was learned.
 *
 * **`carriesDuration` says whether the event has a span at all**, which is why
 * `duration_minutes` is nullable on the wire: a resource that was viewed
 * happened at an instant, and recording "0 minutes" for it would claim a
 * measured zero-length session. The flag lets a row say "recorded as an event"
 * rather than printing a dash next to a number it does not have.
 *
 * **None of these implies understanding.** `resource_viewed` records that a page
 * was opened, which is the weakest member of the set, and a screen must not
 * group it with `concept_learned` as though the two were the same evidence.
 * `coding_activity` is named after what it is — a recorded code event — and never
 * by what it produced.
 */
export interface ActivityTypeMeta {
  label: string
  icon: LucideIcon
  /** True when a duration is a span rather than an instant. */
  carriesDuration: boolean
  /** One sentence saying exactly what the record claims. */
  description: string
}

export const ACTIVITY_TYPE_META: Record<LearningActivityType, ActivityTypeMeta> = {
  study_session: {
    label: 'Study session',
    icon: BookMarked,
    carriesDuration: true,
    description:
      'A period of study you recorded. It may carry a duration, and that duration is how long ' +
      'you said it lasted — NEXUS cannot measure it for you.',
  },
  task_completed: {
    label: 'Task completed',
    icon: ClipboardCheck,
    carriesDuration: true,
    description:
      'A task that was marked complete, recorded here as evidence. The record says the task was ' +
      'completed; it does not claim anything about how long it took or what it taught.',
  },
  note_created: {
    label: 'Note created',
    icon: FileCode2,
    carriesDuration: false,
    description:
      'A note was written and is linked to this skill. A note is a record of something you ' +
      'wrote down, not a measure of understanding.',
  },
  resource_viewed: {
    label: 'Resource viewed',
    icon: Eye,
    carriesDuration: false,
    description:
      'A resource was opened. This is the weakest kind of evidence in the set — it records that ' +
      'a page was viewed and nothing more, so it is weighted accordingly and grouped apart.',
  },
  concept_learned: {
    label: 'Concept learned',
    icon: Lightbulb,
    carriesDuration: false,
    description:
      'You recorded that you picked up a concept. The claim is yours: NEXUS stores the statement ' +
      'and cannot verify it.',
  },
  project_completed: {
    label: 'Project completed',
    icon: FolderCheck,
    carriesDuration: false,
    description:
      'A project was marked complete. This names the event that was recorded, not a claim that ' +
      'the project succeeded.',
  },
  coding_activity: {
    label: 'Coding activity',
    icon: MousePointerClick,
    carriesDuration: false,
    description:
      'A recorded code event, usually derived from a repository. A commit is a code event; it is ' +
      'not a task completion and this surface never renders it as one.',
  },
}

/* --------------------------------------------------------------- level source */

/**
 * Who is allowed to claim a number for a skill level.
 *
 * This is the single most important honesty control in the surface. A
 * `user_defined` level is a claim the person is making and NEXUS merely
 * records; a `system_estimate` is an inference it must be able to show its
 * working for, and it carries `evidence_count` and `confidence` beside it. There
 * is no third member and no "unattributed" fallback, because a level nobody can
 * attribute is not a level.
 */
export interface LevelSourceMeta {
  label: string
  /** The phrase that goes directly beside the number: "3 of 5, self-assessed". */
  phrase: string
  icon: LucideIcon
  tone: 'neutral' | 'info'
  description: string
}

export const LEVEL_SOURCE_META: Record<SkillLevelSource, LevelSourceMeta> = {
  user_defined: {
    label: 'Self-assessed',
    phrase: 'self-assessed by you',
    icon: UserRound,
    tone: 'neutral',
    description:
      'You set this level yourself and NEXUS records it without adjusting it. No estimate and ' +
      'no evidence figure is attached to a level you claimed.',
  },
  system_estimate: {
    label: 'NEXUS estimate',
    phrase: 'estimated by NEXUS from recorded activities',
    icon: Sigma,
    tone: 'info',
    description:
      'NEXUS derived this level from the learning activities recorded against the skill. The ' +
      'evidence count and the confidence beside it say how much that derivation rests on — ' +
      'the backend refuses to offer an estimate at all below three activities.',
  },
}

/* --------------------------------------------------------------- level wording */

/**
 * The words a level sentence must contain, given who said it.
 *
 * Exported as its own function because the same rule binds a skill card, a gap
 * row and the development-areas panel, and three hand-written variants of
 * "3/5" would eventually disagree about who the number belonged to.
 */
export function levelSourcePhrase(source: SkillLevelSource): string {
  return LEVEL_SOURCE_META[source].phrase
}

/**
 * The order activities are grouped in when a trail or a window is summarised.
 *
 * Fixed rather than alphabetical so the strongest kinds of evidence read first
 * and `resource_viewed` — the weakest — is always the last thing in the list
 * rather than wherever the alphabet put it.
 */
export const ACTIVITY_TYPE_ORDER: readonly LearningActivityType[] = [
  'concept_learned',
  'study_session',
  'project_completed',
  'task_completed',
  'note_created',
  'coding_activity',
  'resource_viewed',
]

/**
 * How many distinct days of recorded activity a streak band draws.
 *
 * Two weeks: long enough to show whether a pattern exists, short enough that
 * every square stays large enough to read as a day. It is a presentation
 * constant and is never confused with the window a count covers.
 */
export const STREAK_BAND_DAYS = 14