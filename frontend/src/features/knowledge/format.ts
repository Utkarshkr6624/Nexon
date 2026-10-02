/**
 * The knowledge surface's formatting helpers, re-exported from one home.
 *
 * They live in `types/knowledge.ts` because the shared contract puts them
 * there and every page imports them from that module; this file exists so a
 * component inside `features/knowledge/` can reach the same functions without
 * a second import path. **Nothing is defined here** — a helper that existed in
 * both places would be two helpers that eventually disagree.
 */

export {
  formatKnowledgeDate,
  formatKnowledgeDateTime,
  formatRelative,
  summarise,
} from '@/types/knowledge'