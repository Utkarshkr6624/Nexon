/**
 * The note reader: rendered Markdown plus the one component that shows it.
 *
 * **`dangerouslySetInnerHTML` is unavoidable here** — the whole point of the
 * surface is showing formatted Markdown — and it is also the only place in the
 * app where user-authored text becomes HTML. The mitigation is that
 * {@link renderMarkdown} never passes source HTML through: every character of
 * user text is escaped before any tag is emitted, and the generated string is
 * sanitised again on the way out, so a note containing `<script>` or a
 * `javascript:` link renders as the literal text it is.
 *
 * The parser is deliberately small — headings, emphasis, code, lists,
 * checklists, blockquotes, rules, links. It is a Phase 5 surface, not a
 * CommonMark implementation, and every construct it does not understand is
 * rendered as plain text rather than guessed at.
 *
 * There is no `@tailwindcss/typography` installed, so the block styling is done
 * with descendant variants against the existing tokens.
 */
import { renderMarkdown } from '@/types/knowledge'
import { cn } from '@/lib/utils'

export interface MarkdownViewProps {
  /** Markdown source. `null`/empty renders nothing rather than an empty box. */
  content: string | null | undefined
  className?: string
}

export function MarkdownView({ content, className }: MarkdownViewProps) {
  const html = renderMarkdown(content)
  if (!html) return null

  return (
    <div
      className={cn(
        'space-y-3 text-sm leading-relaxed text-foreground',
        '[&_h1]:text-xl [&_h1]:font-semibold [&_h1]:tracking-tight',
        '[&_h2]:text-lg [&_h2]:font-semibold [&_h2]:tracking-tight',
        '[&_h3]:text-base [&_h3]:font-semibold',
        '[&_h4]:text-sm [&_h4]:font-semibold',
        '[&_h5]:text-sm [&_h5]:font-medium [&_h6]:text-xs [&_h6]:font-medium [&_h6]:uppercase',
        '[&_h6]:text-muted-foreground [&_h6]:tracking-wide',
        '[&_p]:whitespace-pre-wrap',
        '[&_a]:text-primary [&_a]:underline [&_a]:underline-offset-2',
        '[&_strong]:font-semibold',
        '[&_ul]:list-disc [&_ul]:space-y-1 [&_ul]:pl-5',
        '[&_ol]:list-decimal [&_ol]:space-y-1 [&_ol]:pl-5',
        '[&_li_p]:my-0',
        '[&_input[type=checkbox]]:mr-2 [&_input[type=checkbox]]:align-text-top',
        '[&_.task-list-item-complete]:text-muted-foreground [&_.task-list-item-complete]:line-through',
        '[&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
        '[&_hr]:border-border',
        '[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.8125rem]',
        '[&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:bg-muted [&_pre]:p-3 [&_pre]:text-xs',
        '[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-xs',
        className,
      )}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}