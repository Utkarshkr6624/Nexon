import {
  BarChart3,
  BrainCircuit,
  Briefcase,
  CalendarDays,
  Code2,
  FlaskConical,
  FolderKanban,
  GraduationCap,
  LayoutDashboard,
  ListTodo,
  Search,
  Settings,
  Sparkles,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

/**
 * Every destination in the product is declared once, here. The sidebar, the
 * command palette, the breadcrumbs and the placeholder page bodies all render
 * from this registry, so adding a module is a single-file change.
 */

export interface ModuleCapability {
  title: string
  description: string
}

export interface ModuleMetric {
  label: string
  hint: string
}

export interface ModuleDefinition {
  /** Route path, unique and stable. */
  to: string
  label: string
  /** Page-header copy. Specific to the module, never generic. */
  summary: string
  /** Long-form explanation of what ships when the module is built. */
  vision: string
  phase: number
  icon: LucideIcon
  capabilities: ModuleCapability[]
  metrics: ModuleMetric[]
  /** Extra search terms for the command palette. */
  keywords: string[]
}

export interface NavGroup {
  id: string
  label: string
  items: ModuleDefinition[]
}

export const MODULES: ModuleDefinition[] = [
  {
    to: '/dashboard',
    label: 'Dashboard',
    summary:
      'Your cross-module briefing: what needs attention today, which surfaces are live, and the state of the platform underneath.',
    vision:
      'The dashboard is the only module that ships in Phase 1, and it stays deliberately thin until the modules around it have real data to report. It reads live service health from the backend today; once Projects, Tasks and Planner exist it becomes the daily triage surface, surfacing overdue work, stale notes and the decisions waiting on you.',
    phase: 1,
    icon: LayoutDashboard,
    keywords: ['home', 'overview', 'today', 'briefing'],
    capabilities: [
      {
        title: 'Cross-module briefing',
        description:
          'One view that ranks what needs a decision rather than repeating every list in the product.',
      },
      {
        title: 'Live service health',
        description: 'Real status, version and database latency read from GET /api/v1/health.',
      },
      {
        title: 'Surface readiness',
        description:
          'Honest per-module readiness so you always know what is live and what is still planned.',
      },
    ],
    metrics: [
      { label: 'Live modules', hint: 'Counted from the module registry' },
      { label: 'Planned modules', hint: 'Scheduled across Phases 2 to 10' },
      { label: 'Open decisions', hint: 'Requires the Decision module' },
      { label: 'Focus hours', hint: 'Requires Planner time blocks' },
    ],
  },
  {
    to: '/projects',
    label: 'Projects',
    summary: 'Long-running efforts with their own goals, milestones, files and decision history.',
    vision:
      'Projects give NEXUS a place to hold work that outlives a single task: an outcome, a health signal, linked notes and the decisions that shaped the direction. Each project tracks momentum without pretending to be a delivery methodology — the goal is context you can return to, not ceremony.',
    phase: 2,
    icon: FolderKanban,
    keywords: ['work', 'initiatives', 'portfolio', 'spaces'],
    capabilities: [
      {
        title: 'Outcome and scope',
        description:
          'A stated outcome, a working definition of done and a health signal per project.',
      },
      {
        title: 'Linked records',
        description:
          'Tasks, knowledge entries and decisions attach to the project that spawned them.',
      },
      {
        title: 'Decision history',
        description: 'Every call made inside a project is captured with its reasoning and date.',
      },
    ],
    metrics: [
      { label: 'Active projects', hint: 'Requires project records' },
      { label: 'At risk', hint: 'Requires health signals' },
      { label: 'Shipped this quarter', hint: 'Requires outcome tracking' },
    ],
  },
  {
    to: '/tasks',
    label: 'Tasks',
    summary:
      'The execution layer: individual commitments with owners, due dates and links to the project or goal they serve.',
    vision:
      'Tasks are deliberately small and boring — capture fast, triage deliberately. Each one can be attached to a project, a goal or a knowledge entry, which is what lets the analytics module later answer "where did the time actually go" without you maintaining a spreadsheet alongside the product.',
    phase: 2,
    icon: ListTodo,
    keywords: ['todo', 'work', 'actions', 'checklist'],
    capabilities: [
      {
        title: 'Fast capture',
        description: 'Add a task in one line, then enrich it with links when the context arrives.',
      },
      {
        title: 'Triage views',
        description:
          'Filter by due date, project, status and energy cost without changing the underlying data.',
      },
      {
        title: 'Effort tracking',
        description:
          'Optional effort estimates feed the analytics module rather than replacing it.',
      },
    ],
    metrics: [
      { label: 'Open tasks', hint: 'Requires task records' },
      { label: 'Due this week', hint: 'Requires due dates' },
      { label: 'Completed this week', hint: 'Requires completion events' },
    ],
  },
  {
    to: '/planner',
    label: 'Planner',
    summary:
      'Time blocking across real weeks: what you intend to work on, for how long, and what it displaces.',
    vision:
      'The planner answers a question task lists cannot: not "what is left" but "what will this week actually contain". Blocks are laid against a weekly capacity you control, conflicts are surfaced explicitly, and completed focus time is the raw signal the analytics module will later interpret.',
    phase: 3,
    icon: CalendarDays,
    keywords: ['calendar', 'schedule', 'week', 'time', 'blocks'],
    capabilities: [
      {
        title: 'Weekly capacity',
        description: 'A capacity you set, so over-committed weeks are visible before they happen.',
      },
      {
        title: 'Time blocks',
        description: 'Drag work into blocks and see conflicts before they become a broken week.',
      },
      {
        title: 'Focus log',
        description: 'Finished blocks record real elapsed time for later analysis.',
      },
    ],
    metrics: [
      { label: 'Blocks this week', hint: 'Requires planner blocks' },
      { label: 'Capacity used', hint: 'Requires a weekly capacity setting' },
      { label: 'Focus hours logged', hint: 'Requires completed blocks' },
    ],
  },
  {
    to: '/knowledge',
    label: 'Knowledge',
    summary:
      'A durable personal knowledge base: notes, references and claims that other modules can cite.',
    vision:
      'Knowledge is the substrate the rest of NEXUS reasons over. Notes are written once, linked to projects and decisions, and stay quotable so the assistant and analytics modules can point back at your own words instead of a generic summary. Structure stays lightweight: a good link graph beats a rigid hierarchy.',
    phase: 4,
    icon: BrainCircuit,
    keywords: ['notes', 'wiki', 'writing', 'vault'],
    capabilities: [
      {
        title: 'Linked notes',
        description: 'Bidirectional links between notes, projects, tasks and decisions.',
      },
      {
        title: 'Citations',
        description: 'Other modules reference a note directly instead of paraphrasing it.',
      },
      {
        title: 'Retrieval index',
        description:
          'Notes are chunked and indexed so search and the assistant stay grounded in your text.',
      },
    ],
    metrics: [
      { label: 'Notes', hint: 'Requires note records' },
      { label: 'Linked notes', hint: 'Requires the link graph' },
      { label: 'Indexed passages', hint: 'Requires the retrieval index' },
    ],
  },
  {
    to: '/search',
    label: 'Search',
    summary:
      'One query across every surface you own — projects, tasks, notes, decisions and career history.',
    vision:
      'Search is the retrieval front door for the whole platform. A single ranked result set spans structured records and indexed prose, with filters per source and a keyboard-first interface so finding something is faster than remembering where you filed it. The command palette in the top bar is its navigational sibling.',
    phase: 4,
    icon: Search,
    keywords: ['find', 'query', 'lookup', 'global'],
    capabilities: [
      {
        title: 'Unified results',
        description: 'Structured records and prose passages ranked into one list.',
      },
      {
        title: 'Source filters',
        description: 'Narrow to projects, tasks or notes without rewriting the query.',
      },
      {
        title: 'Keyboard first',
        description: 'Open, query, filter and jump without leaving the home row.',
      },
    ],
    metrics: [
      { label: 'Indexed sources', hint: 'Requires the search index' },
      { label: 'Queries this month', hint: 'Requires query logging' },
      { label: 'Result latency', hint: 'Measured once the index is live' },
    ],
  },
  {
    to: '/analytics',
    label: 'Analytics',
    summary:
      'Where your time and attention actually went, computed from data you produced rather than guessed at.',
    vision:
      'Analytics only earns trust by being derived from real records: completion events, focus blocks, decision timestamps and knowledge growth. Every figure links back to the records behind it, and every metric states its own limitations. Nothing here is decorative — if the underlying data does not exist yet, the module says so instead of drawing a chart.',
    phase: 5,
    icon: BarChart3,
    keywords: ['metrics', 'insights', 'reports', 'trends', 'stats'],
    capabilities: [
      {
        title: 'Traceable metrics',
        description: 'Every aggregate links to the records that produced it.',
      },
      {
        title: 'Attention over time',
        description: 'Focus blocks and completions combined into an honest picture of capacity.',
      },
      {
        title: 'Stated limits',
        description: 'Each chart declares its sample size and known blind spots.',
      },
    ],
    metrics: [
      { label: 'Tasks completed', hint: 'Requires completion events' },
      { label: 'Focus hours', hint: 'Requires planner blocks' },
      { label: 'Knowledge growth', hint: 'Requires note history' },
    ],
  },
  {
    to: '/developer',
    label: 'Developer',
    summary:
      'Your engineering footprint in one place: repositories, commits, pull requests and the systems you maintain.',
    vision:
      'The developer surface connects local git history to the work records you keep here, so "what did I actually build" is answerable without a separate dashboard. Repository metadata is read locally — no hosted service required — and every derived number is traceable back to the commits it came from.',
    phase: 6,
    icon: Code2,
    keywords: ['git', 'code', 'repos', 'engineering', 'commits'],
    capabilities: [
      {
        title: 'Local repository scan',
        description: 'Reads git history from disk; no hosted account or token required.',
      },
      {
        title: 'Work attribution',
        description: 'Links commits and pull requests to the tasks and projects they served.',
      },
      {
        title: 'System inventory',
        description: 'A live list of the services and repositories you actually maintain.',
      },
    ],
    metrics: [
      { label: 'Repositories', hint: 'Requires a local repository scan' },
      { label: 'Commits (30d)', hint: 'Requires git history' },
      { label: 'Open pull requests', hint: 'Requires a forge integration' },
    ],
  },
  {
    to: '/learning',
    label: 'Learning',
    summary:
      'Deliberate practice: courses, reading queues, spaced review and evidence that the knowledge stuck.',
    vision:
      'Learning tracks what you are trying to learn, not what you have bookmarked. Each track pairs source material with review intervals, and notes captured during study flow back into the knowledge base. Progress is measured by recall and applied work rather than by the number of tabs left open.',
    phase: 7,
    icon: GraduationCap,
    keywords: ['study', 'courses', 'reading', 'review', 'skills'],
    capabilities: [
      {
        title: 'Tracks and sources',
        description: 'Courses and reading queues attached to a declared learning goal.',
      },
      {
        title: 'Spaced review',
        description: 'Review intervals scheduled from recall quality, not a fixed calendar.',
      },
      {
        title: 'Applied evidence',
        description: 'Projects and tasks link back to the track they exercised.',
      },
    ],
    metrics: [
      { label: 'Active tracks', hint: 'Requires learning tracks' },
      { label: 'Due for review', hint: 'Requires review scheduling' },
      { label: 'Completion rate', hint: 'Requires source progress' },
    ],
  },
  {
    to: '/career',
    label: 'Career',
    summary:
      'The long arc: goals, evidence, contacts and a running record of how your work has grown.',
    vision:
      'Career is the slowest-moving module and the one with the longest half-life. It holds the goals you are working toward, the evidence that supports them, and the conversations and opportunities attached to them, so that a review or an application is assembled from real history instead of reconstructed memory.',
    phase: 8,
    icon: Briefcase,
    keywords: ['growth', 'goals', 'review', 'profile', 'history'],
    capabilities: [
      {
        title: 'Goals and evidence',
        description: 'Long-horizon goals backed by artefacts pulled from real work.',
      },
      {
        title: 'Opportunity log',
        description: 'Conversations, applications and outcomes recorded with dates.',
      },
      {
        title: 'Narrative export',
        description: 'A generated summary assembled from your own records.',
      },
    ],
    metrics: [
      { label: 'Active goals', hint: 'Requires career goals' },
      { label: 'Evidence items', hint: 'Requires linked artefacts' },
      { label: 'Last review', hint: 'Requires review history' },
    ],
  },
  {
    to: '/assistant',
    label: 'AI Assistant',
    summary:
      'A local-first assistant that answers from your own records and proposes work, not from a generic model.',
    vision:
      'The assistant is the reason the rest of NEXUS stores data in a structured way. It reads across projects, tasks, notes and decisions you created, cites what it used, and proposes actions you approve rather than actions it takes. Inference runs against a model you control, and nothing leaves the machine unless you say so.',
    phase: 9,
    icon: Sparkles,
    keywords: ['ai', 'assistant', 'ask', 'copilot', 'llm'],
    capabilities: [
      {
        title: 'Grounded answers',
        description: 'Every answer cites the note, task or decision it was drawn from.',
      },
      {
        title: 'Proposed actions',
        description: 'Suggests changes for you to approve; it does not write silently.',
      },
      {
        title: 'Local inference',
        description: 'Runs against a model you host, with no external calls by default.',
      },
    ],
    metrics: [
      { label: 'Conversations', hint: 'Requires assistant sessions' },
      { label: 'Grounded answers', hint: 'Requires citation tracking' },
      { label: 'Local model', hint: 'Requires a configured backend' },
    ],
  },
  {
    to: '/experiments',
    label: 'Experiments',
    summary:
      'Ship small, measure honestly, and keep or kill the idea on evidence rather than on enthusiasm.',
    vision:
      'Experiments holds the ideas that are not yet commitments: a hypothesis, a bounded build, a success metric and a decision at the end. The module makes killing a failed experiment a first-class outcome, so the backlog stays small and the kept ideas carry the evidence that justified them.',
    phase: 10,
    icon: FlaskConical,
    keywords: ['experiments', 'ideas', 'hypothesis', 'validate', 'labs'],
    capabilities: [
      {
        title: 'Hypothesis and metric',
        description: 'Each experiment states what it expects to move and by how much.',
      },
      {
        title: 'Bounded scope',
        description: 'An explicit cap keeps exploratory work from leaking into committed work.',
      },
      {
        title: 'Keep or kill',
        description:
          'A recorded verdict either promotes the idea or archives it with its evidence.',
      },
    ],
    metrics: [
      { label: 'Running experiments', hint: 'Requires experiment records' },
      { label: 'Kept', hint: 'Requires recorded verdicts' },
      { label: 'Killed', hint: 'Requires recorded verdicts' },
    ],
  },
]

export const SETTINGS_MODULE: ModuleDefinition = {
  to: '/settings',
  label: 'Settings',
  summary: 'Your account, appearance and the platform preferences that apply everywhere.',
  vision:
    'Settings is split into two halves. Appearance, theme and session preferences are live now because they need no backend data. Everything that depends on a stored profile or a notification stream lands with the modules that own the underlying records, rather than being stubbed here.',
  phase: 1,
  icon: Settings,
  keywords: ['preferences', 'account', 'profile', 'theme', 'configuration'],
  capabilities: [
    {
      title: 'Appearance (live)',
      description: 'Light, dark or system theme, persisted to this browser only.',
    },
    {
      title: 'Session (live)',
      description: 'Sign out of this device and inspect the session the shell is holding.',
    },
    {
      title: 'Account preferences',
      description:
        'Profile, notification and data-retention settings arrive with the modules that need them.',
    },
  ],
  metrics: [
    { label: 'Theme', hint: 'Stored in this browser' },
    { label: 'Session', hint: 'Held in this browser' },
    { label: 'Notifications', hint: 'Requires a notification stream' },
  ],
}

export interface NavGroupDefinition {
  id: string
  label: string
  items: ModuleDefinition[]
}

/**
 * Resolves a module by route path. Throws on an unknown path, which can only
 * happen if a route and the registry have drifted apart.
 */
export function getModule(path: string): ModuleDefinition {
  const match = MODULES.find((module) => module.to === path)
  if (!match) throw new Error(`Unknown module path: ${path}`)
  return match
}

export const NAV_GROUPS: NavGroupDefinition[] = [
  { id: 'overview', label: 'Overview', items: [getModule('/dashboard')] },
  {
    id: 'work',
    label: 'Work',
    items: [getModule('/projects'), getModule('/tasks'), getModule('/planner')],
  },
  {
    id: 'intelligence',
    label: 'Intelligence',
    items: [getModule('/knowledge'), getModule('/analytics'), getModule('/search')],
  },
  {
    id: 'growth',
    label: 'Growth',
    items: [getModule('/developer'), getModule('/learning'), getModule('/career')],
  },
  {
    id: 'platform',
    label: 'Platform',
    items: [getModule('/assistant'), getModule('/experiments')],
  },
]

/** Flat, ordered list used by the command palette. */
export const ALL_NAV_ITEMS: ModuleDefinition[] = [
  ...NAV_GROUPS.flatMap((group) => group.items),
  SETTINGS_MODULE,
]

export function findModule(path: string): ModuleDefinition | undefined {
  return ALL_NAV_ITEMS.find((module) => module.to === path)
}
