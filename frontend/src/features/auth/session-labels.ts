/**
 * Human-readable labels for the active-sessions list.
 *
 * A stored `user_agent` is a several-hundred-character fingerprint that means
 * nothing to the person trying to decide whether a session is theirs. These
 * helpers reduce one to "Chrome on Windows" plus an icon hint, and they do it
 * from a closed vocabulary: an agent string is never echoed into the UI, so a
 * malformed, hostile or simply unrecognised value costs nothing but a generic
 * label instead of dumping a wall of text into the page.
 */

export interface SessionLabel {
  browser: string
  platform: string
  /** Ready to render, e.g. "Chrome on Windows" or "Unknown device". */
  label: string
  /** lucide-react icon name; the sessions list owns the icon lookup. */
  iconHint: string
}

const UNKNOWN = 'Unknown'

/** Real agents land between roughly 60 and 400 characters. */
const MAX_USER_AGENT_LENGTH = 512

interface Signature {
  readonly label: string
  readonly test: RegExp
}

/**
 * Order matters. Every Chromium browser also advertises `Chrome/`, Safari
 * advertises `Safari/` and Firefox does not appear in Safari strings at all, so
 * the derivatives have to be matched before the browser they impersonate or a
 * session on Edge reads as Chrome.
 */
const BROWSERS: readonly Signature[] = [
  { label: 'Edge', test: /\b(?:Edg|Edge)\// },
  { label: 'Opera', test: /\b(?:OPR|OPiOS|Opera)\// },
  { label: 'Samsung Internet', test: /\bSamsungBrowser\// },
  { label: 'Chrome', test: /\b(?:Chrome|CriOS)\// },
  { label: 'Firefox', test: /\b(?:Firefox|FxiOS)\// },
  { label: 'Safari', test: /\bSafari\// },
]

/** Tablets first: an iPad also reports iOS, and an Android tablet reports Android. */
const PLATFORMS: readonly Signature[] = [
  { label: 'iPadOS', test: /\b(?:iPad|PlayBook|Silk)\b/ },
  { label: 'iOS', test: /\b(?:iPhone|iPod)\b/ },
  { label: 'Android', test: /\bAndroid\b/ },
  { label: 'ChromeOS', test: /\bCrOS\b/ },
  { label: 'Windows', test: /\bWindows\b/ },
  { label: 'macOS', test: /\b(?:Mac OS X|Macintosh)\b/ },
  { label: 'Linux', test: /\b(?:Linux|X11)\b/ },
  { label: 'BSD', test: /\b(?:FreeBSD|OpenBSD|NetBSD)\b/ },
]

/** Non-browser clients share no marker with a browser, so they are matched separately. */
const AUTOMATED_CLIENT = /\b(?:bot|crawler|spider|slurp|curl|wget|libwww|python-requests|httpx|okhttp|node-fetch|axios|PostmanRuntime|Insomnia|HeadlessChrome)\b/i

/** Handheld form, for the icon choice; a tablet is simply a non-handheld iPad. */
const HANDHELD_FORM = /\b(?:Mobile|iPhone|iPod|Touch)\b/

const UNKNOWN_LABEL: SessionLabel = Object.freeze({
  browser: UNKNOWN,
  platform: UNKNOWN,
  label: 'Unknown device',
  iconHint: 'globe',
})

/** Trims, and rejects anything that cannot be a real agent string. */
function normalise(userAgent: string | null | undefined): string | null {
  if (typeof userAgent !== 'string') return null
  const trimmed = userAgent.trim()
  return trimmed.length === 0 || trimmed.length > MAX_USER_AGENT_LENGTH ? null : trimmed
}

function match(signatures: readonly Signature[], agent: string): string | null {
  for (const signature of signatures) {
    if (signature.test.test(agent)) return signature.label
  }
  return null
}

function iconHintFor(agent: string, platform: string): string {
  if (AUTOMATED_CLIENT.test(agent)) return 'bot'
  if (platform === 'Android') return HANDHELD_FORM.test(agent) ? 'smartphone' : 'tablet'
  if (platform === 'iOS') return 'smartphone'
  if (platform === 'iPadOS') return 'tablet'
  if (HANDHELD_FORM.test(agent)) return 'smartphone'
  return platform === UNKNOWN ? 'globe' : 'monitor'
}

function composeLabel(browser: string, platform: string): string {
  if (browser === UNKNOWN && platform === UNKNOWN) return 'Unknown device'
  if (browser === UNKNOWN) return platform
  if (platform === UNKNOWN) return browser
  return `${browser} on ${platform}`
}

/**
 * Reduce a stored `user_agent` to something recognisable. Never throws, and
 * never returns part of the input: an absent, empty, oversized or unrecognised
 * agent all collapse to the same "Unknown device" entry.
 *
 * Known limit: since iPadOS 13 an iPad reports itself as a Macintosh, so it is
 * labelled macOS with a desktop icon. There is no way around that from the
 * agent string alone, and a wrong guess here is safer than a confident one.
 */
export function describeUserAgent(userAgent: string | null | undefined): SessionLabel {
  const agent = normalise(userAgent)
  if (agent === null) return UNKNOWN_LABEL

  const browser = match(BROWSERS, agent) ?? UNKNOWN
  const platform = match(PLATFORMS, agent) ?? UNKNOWN

  return {
    browser,
    platform,
    label: composeLabel(browser, platform),
    iconHint: iconHintFor(agent, platform),
  }
}

const MINUTE_MS = 60_000
const HOUR_MS = 60 * MINUTE_MS
const DAY_MS = 24 * HOUR_MS
const MONTH_MS = 30 * DAY_MS
/** Weeks run to five before the label moves to months, so "3 weeks ago" is a
 * thing a person says and "7 weeks ago" is not. */
const MAX_WEEKS = 5

function ago(count: number, unit: string): string {
  return count === 1 ? `1 ${unit} ago` : `${count} ${unit}s ago`
}

/**
 * "now", "2 hours ago", "3 days ago" for a session list.
 *
 * Every path that could render `NaN` is rejected up front rather than guarded
 * per branch: `new Date('not a date')` is an Invalid Date, and subtracting it
 * from `now` yields NaN, which formats as "NaN minutes ago". Missing input and
 * unparseable input are also different answers — a session that has never
 * happened is "Never", one whose timestamp is unreadable is "Unknown".
 *
 * A timestamp in the future means the server and the browser clocks disagree,
 * not that something happened yet, so it reads as "now".
 */
export function formatRelativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (typeof iso !== 'string' || iso.trim().length === 0) return 'Never'

  const then = new Date(iso)
  if (Number.isNaN(then.getTime()) || Number.isNaN(now.getTime())) return 'Unknown'

  const elapsed = now.getTime() - then.getTime()
  if (elapsed < MINUTE_MS) return 'now'

  const minutes = Math.floor(elapsed / MINUTE_MS)
  if (minutes < 60) return ago(minutes, 'minute')

  const hours = Math.floor(elapsed / HOUR_MS)
  if (hours < 24) return ago(hours, 'hour')

  const days = Math.floor(elapsed / DAY_MS)
  if (days < 7) return ago(days, 'day')

  if (days < MAX_WEEKS * 7) return ago(Math.floor(days / 7), 'week')

  const months = Math.floor(elapsed / MONTH_MS)
  if (months < 12) return ago(months, 'month')

  return ago(Math.floor(days / 365), 'year')
}
