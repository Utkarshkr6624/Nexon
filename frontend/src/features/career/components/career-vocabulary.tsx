/**
 * The career surface's vocabulary: what each evidence type, record kind and
 * evidence source is called, which icon carries it, which tone it wears and one
 * sentence saying exactly what the record claims.
 *
 * **This is a `.tsx` file with no JSX in it, and that is deliberate.** The
 * project runs `react-refresh/only-export-components`, which reports a file that
 * exports a component *and* a non-component; `allowConstantExport` is on but the
 * plugin only exempts literals, so an object of metadata cannot sit beside the
 * badges that consume it. The rule's own error message gives the remedy — "use a
 * new file to share constants" — and this file belongs under `components/`
 * because these tables describe how this surface renders its vocabulary.
 *
 * ## Nothing here was written by NEXUS
 *
 * The career profile is entirely user-controlled, and these descriptions are the
 * place that fact is discharged. `certification` is in the evidence vocabulary
 * **because the user may hold one** — NEXUS issues nothing, infers nothing and
 * never fills a date, an employer or a credential the user did not supply. The
 * `certification` description says so explicitly, because a reader who sees the
 * word in a list of things "NEXUS recorded" would otherwise be misled.
 *
 * ## `repository_activity` is named after the record it came from
 *
 * "6 commits touched Python files in this repository" is true and derivable;
 * "delivered 6 Python projects" is a different claim with no field behind it. The
 * description therefore names commits and scans, and says explicitly that the row
 * is not a task count.
 *
 * ## Source is never blurred
 *
 * `manual` and a subsystem name are two different provenances and they get two
 * different chips. A derived row presented as typed would claim a provenance the
 * row does not have, and a typed row presented as derived would dismiss
 * something the user actually asserted.
 */

import {
  Award,
  Briefcase,
  FolderCheck,
  GraduationCap,
  Medal,
  PackageCheck,
  Rocket,
  Sigma,
  Sparkles,
  Target,
  UserRound,
  type LucideIcon,
} from 'lucide-react'

import {
  INSUFFICIENT_DATA_MESSAGE,
  MAX_SKILL_LEVEL,
  type CareerEvidenceType,
  type CareerRecordKind,
  type SkillLevelSource,
} from '@/types/learning'

/**
 * The title every insufficient-data state on this surface reads.
 *
 * One sentence, one meaning: the measurement could not be made. It is **not** a
 * zero, and a card carrying it must not also print a figure.
 *
 * Restated as a literal rather than referenced from `INSUFFICIENT_DATA_MESSAGE`,
 * because initialising an upper-case export from an upper-case *identifier* makes
 * `react-refresh/only-export-components` read this file as one that exports a
 * React component. The annotation is a compile-time tie to the wire constant, so
 * the two cannot drift apart.
 */
export const NOT_ENOUGH_DATA_TITLE: typeof INSUFFICIENT_DATA_MESSAGE = 'Not enough data yet.'

/* ------------------------------------------------------------ evidence types */

/**
 * A thing worth putting in front of someone who is deciding about the user.
 *
 * Every description says **what the row is**, in the same register the record
 * itself uses. None of them says what it proves: a project marked complete is an
 * event in a project row, and this file will not let a screen upgrade it.
 */
export interface CareerEvidenceTypeMeta {
  label: string
  icon: LucideIcon
  description: string
}

export const CAREER_EVIDENCE_TYPE_META: Record<CareerEvidenceType, CareerEvidenceTypeMeta> = {
  project_completed: {
    label: 'Project completed',
    icon: FolderCheck,
    description:
      'A project marked complete. The row records that the project reached that status — it ' +
      'does not measure what the project achieved.',
  },
  feature_shipped: {
    label: 'Feature shipped',
    icon: PackageCheck,
    description:
      'A feature recorded as shipped, entered by you or derived from a project. "Shipped" is ' +
      'the word you or a project record used.',
  },
  repository_activity: {
    label: 'Repository activity',
    icon: Rocket,
    description:
      'Activity a repository scan recorded: commits, branches and changed lines. This names ' +
      'code events — it is not a count of tasks delivered, and it is not a statement about ' +
      'anyone’s time.',
  },
  skill_activity: {
    label: 'Skill activity',
    icon: Sparkles,
    description:
      'A learning activity recorded against one of your skills. The linked skill row carries ' +
      'the level and where that level came from.',
  },
  learning_milestone: {
    label: 'Learning milestone',
    icon: Target,
    description:
      'A milestone reached against a learning goal. The goal’s own progress figure is the one ' +
      'you set, and this row does not restate it as a measurement.',
  },
  certification: {
    label: 'Certification',
    icon: Award,
    description:
      'A certification **you supplied**, with the title and date you gave it. NEXUS issues no ' +
      'credential, verifies none and infers none — it stores what you entered.',
  },
  achievement: {
    label: 'Achievement',
    icon: Medal,
    description:
      'An achievement you wrote into your profile. It is stored verbatim, and nothing here ' +
      'expands, reworded or extrapolated it.',
  },
}

/**
 * The order evidence groups are shown in.
 *
 * Fixed rather than alphabetical so a reader's eye lands on the dated record
 * types first and the self-entered ones last, and so a grouping is stable between
 * a window change and its refetch.
 */
export const CAREER_EVIDENCE_TYPE_ORDER: readonly CareerEvidenceType[] = [
  'project_completed',
  'feature_shipped',
  'repository_activity',
  'skill_activity',
  'learning_milestone',
  'certification',
  'achievement',
]

/* -------------------------------------------------------------- record kinds */

/**
 * A line on the career profile that is a *record* rather than an achievement.
 *
 * Education, work experience and certifications are the CV section, so they are
 * never rendered in the same weight as the achievements section: a certification
 * you hold is a dated record, and dressing it as an accomplishment would be a
 * claim the row does not make.
 */
export interface CareerRecordKindMeta {
  label: string
  icon: LucideIcon
  description: string
}

export const CAREER_RECORD_KIND_META: Record<CareerRecordKind, CareerRecordKindMeta> = {
  education: {
    label: 'Education',
    icon: GraduationCap,
    description:
      'A course, programme or qualification you listed. The dates are the ones you gave; ' +
      'NEXUS never looks one up and never fills in a graduation year.',
  },
  experience: {
    label: 'Experience',
    icon: Briefcase,
    description:
      'A role you listed. An end date you left empty means the role is current, which is a ' +
      'fact about the record rather than a missing value.',
  },
  certification: {
    label: 'Certification',
    icon: Award,
    description:
      'A certification you hold, listed with the issuer and dates you supplied. NEXUS issues ' +
      'nothing and verifies nothing.',
  },
}

/* --------------------------------------------------------------------- source */

/**
 * Where an evidence row came from.
 *
 * `manual` is the only value a person sets: anything else names a subsystem that
 * derived the row from a record the person created. Presenting a derived row as
 * typed would forge a provenance, and presenting a typed row as derived would
 * dismiss a claim the user actually made — so the two are separate chips with
 * separate sentences.
 */
export interface EvidenceSourceMeta {
  label: string
  /** The sentence form: "Added by you." / "Recorded by NEXUS from a project." */
  phrase: string
  icon: LucideIcon
  tone: 'neutral' | 'info'
  derived: boolean
}

export const MANUAL_EVIDENCE_SOURCE: EvidenceSourceMeta = {
  label: 'Added by you',
  phrase: 'Added by you. Everything on this row is what you typed.',
  icon: GraduationCap,
  tone: 'neutral',
  derived: false,
}

/** The chip for any source other than `manual`, named after what derived it. */
export function evidenceSourceMeta(source: string | null | undefined): EvidenceSourceMeta {
  const name = source?.trim()
  if (!name || name === 'manual') return MANUAL_EVIDENCE_SOURCE

  const noun = name === 'repository' ? 'repository scans' : `${name} records`
  return {
    label: 'Recorded by NEXUS',
    phrase: `Recorded by NEXUS from ${noun}. You did not write this row; it was derived from a record you created.`,
    icon: Sparkles,
    tone: 'info',
    derived: true,
  }
}

/* --------------------------------------------------------------------- levels */

/**
 * Who is allowed to claim a number for a skill level.
 *
 * The same honesty control the learning surface carries, restated here because
 * a career profile is read by someone deciding whether to trust it: a level the
 * person typed is a claim they are making, a level NEXUS derived is an inference
 * it has to show its working for. There is no third member, because a level
 * nobody can attribute is not a level.
 */
export const CAREER_LEVEL_SOURCE_META: Record<
  SkillLevelSource,
  {
    label: string
    phrase: string
    icon: LucideIcon
    tone: 'neutral' | 'info'
    description: string
  }
> = {
  user_defined: {
    label: 'Self-assessed',
    phrase: 'self-assessed by you',
    icon: UserRound,
    tone: 'neutral',
    description:
      'You set this level yourself and NEXUS records it without adjusting it. No estimate or ' +
      'confidence figure is attached to a level you claimed.',
  },
  system_estimate: {
    label: 'NEXUS estimate',
    phrase: 'estimated by NEXUS from recorded activities',
    icon: Sigma,
    tone: 'info',
    description:
      'NEXUS derived this level from the learning activities recorded against the skill. The ' +
      'evidence count beside it says how much that derivation rests on.',
  },
}

/**
 * The 1–5 scale the backend constrains skill levels with.
 *
 * Stated as a literal with a `satisfies` tie to `MAX_SKILL_LEVEL` for the reason
 * `react-refresh/only-export-components` cannot read a re-export: an
 * upper-case constant initialised from an upper-case identifier is taken for a
 * component. The tie keeps the wire types the single source of truth.
 */
export const CAREER_LEVEL_SCALE = 5 satisfies typeof MAX_SKILL_LEVEL