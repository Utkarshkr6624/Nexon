# Phase 5 — Knowledge Base, Notes, Resources & Knowledge Graph Foundation

> **Status: 🔴 Not started.** Requires Phase 3 (link knowledge to projects and tasks).

---

## PREAMBLE

Before implementing anything, inspect the COMPLETE current repository and understand what was
actually built in Phases 1–4. Do not assume previous architecture exactly matches the original
plan.

### 🚨 MANDATORY PRE-PHASE BUG CHECK

**DO NOT immediately start Phase 5 development.** First perform a complete regression check.

**Frontend:** TypeScript errors, build errors, broken imports, broken routes, console errors,
authentication, protected routes, dashboard, projects, tasks, Kanban, planner, calendar, work
sessions, forms, dialogs, filters, search, dark/light mode, responsive layouts, loading states,
error states.

**Backend:** startup, imports, API routes, authentication, authorization, validation, exception
handling, project APIs, task APIs, planner APIs, calendar APIs, work-session APIs.

**Database:** migration consistency, foreign keys, indexes, constraints, relationships, orphan
records.

**Integration:** React → FastAPI, authentication, project/task integration, task/calendar
integration, planner functionality, date/time handling.

If bugs are found: 1. Document them. 2. Fix them. 3. Re-test them. 4. Only then start Phase 5.

**Do NOT build on top of known broken functionality.** If no meaningful issues are found, explicitly
state that the pre-phase regression check passed.

### PHASE 5 OBJECTIVE

Build the complete NEXUS Knowledge Base. NEXUS should allow users to capture, organize, connect
and retrieve knowledge. The system should support: notes, documents, resources, bookmarks, tags,
categories, relationships, backlinks, references, knowledge graph visualization, linking knowledge
to projects/tasks/learning topics.

**This should NOT be a basic notes CRUD system.** The goal is to create the foundation of a
personal knowledge system that future NEXUS analytics and AI can understand.

### CORE KNOWLEDGE OBJECTS

Support: **1.** Notes **2.** Resources **3.** Bookmarks **4.** Documents **5.** Concepts
**6.** Tags **7.** Relationships.

**Keep these concepts separate.**

A **Note** is user-created knowledge. A **Resource** points to an external/local resource. A
**Bookmark** is a saved URL. A **Document** represents a stored/referenced document. A **Concept**
represents an important knowledge entity. A **Tag** categorizes information. A **Relationship**
connects knowledge objects.

### NOTES

Note fields: `id`, `owner_id`, `title`, `content`, `summary`, `status`, `created_at`,
`updated_at`.

| Statuses |
| --- |
| `DRAFT`, `PUBLISHED`, `ARCHIVED` |

Support create, edit, view, archive, restore, delete. **Do not store everything as one giant
unstructured string if the architecture can support structured content.**

### RICH TEXT EDITOR

Create a polished note editor. Support useful formatting such as: headings, bold, italic,
underline, bullets, numbered lists, code blocks, inline code, quotes, links, checklists.

**If a suitable editor library already exists, use it. If not, choose a lightweight,
well-maintained editor that fits the architecture. Do NOT build a huge editor from scratch
unnecessarily.** The editor should feel professional.

### MARKDOWN SUPPORT

If practical, support Markdown import/export or Markdown-compatible storage. The architecture
should make it possible to create/edit formatted content, preserve content, export later.
**Do not sacrifice editor stability just to add Markdown.**

### AUTOSAVE

Implement autosave carefully. While editing, save changes after a short debounce period. Show:
`Saving...`, `Saved`, `Unsaved changes`, `Save failed`.

**Do not create a database request on every keystroke. Handle network failure gracefully.**

### VERSION HISTORY

Create a basic note revision system. When meaningful edits occur, allow previous versions to be
retained. A revision may contain `note_id`, `content`, `title`, `created_at`, `created_by`.
Provide a revision history interface. Allow viewing previous versions.
**Do NOT implement complex collaborative editing.**

### TAGS

`DSA`, `React`, `AI`, `Machine Learning`, `Python`, `Java`, `FastAPI`, `Database`,
`System Design`. Tags should be reusable, belong to a user, have a name, support filtering.
**Prevent accidental duplicate tags for the same user.**

### CATEGORIES

```text
Programming
├── React
├── Backend
└── Databases

AI
├── Search
├── Knowledge Representation
└── Machine Learning

Personal
├── Ideas
└── Planning
```

**Keep categories simple. Do not create a complex folder system yet.**

### BOOKMARKS

Fields: `id`, `owner_id`, `url`, `title`, `description`, `domain`, `created_at`, `updated_at`.
Allow create, edit, delete, archive. Display domain information.
**Do NOT scrape websites aggressively. Do not introduce external scraping infrastructure. If
metadata extraction is unreliable, allow users to enter title/description manually.**

### RESOURCES

A resource may represent: Article, Video, Course, Documentation, Repository, Paper, Website,
Other. Fields can include `title`, `description`, `url`, `resource_type`, `tags`, `created_at`.
Resources can be connected to notes and concepts.

### DOCUMENTS

Create a document metadata layer. Support metadata such as `filename`, `title`, `description`,
`document_type`, `created_at`, `updated_at`. The actual document ingestion pipeline will be
expanded later. For Phase 5, create the architecture needed to associate documents with
knowledge. **Do NOT build a massive PDF processing pipeline yet.**

### CONCEPTS

```text
Concept: AVL Tree
Description: Self-balancing binary search tree.
Related concepts: Binary Search Tree, Tree Rotation, Balance Factor
```

Concepts should be linkable to Notes, Resources, Projects and Tasks. **This becomes the
foundation of the knowledge graph.**

### KNOWLEDGE RELATIONSHIPS

```text
NOTE     → REFERENCES → NOTE
NOTE     → EXPLAINS   → CONCEPT
CONCEPT  → RELATED_TO → CONCEPT
RESOURCE → SUPPORTS  → CONCEPT
PROJECT  → USES      → CONCEPT
TASK     → REQUIRES  → CONCEPT
```

**Do not hardcode relationship types throughout the frontend. Create an extensible relationship
representation.**

### BACKLINKS

If Note A references Note B, then Note B should show:

```text
Referenced by: Note A
```

**Implement backlinks. This is one of the important features that differentiates this from a
basic notes application.**

### KNOWLEDGE GRAPH

```text
         React
           │
           │
      TypeScript
       /       \
      /         \
  Frontend ─── Vite
      │
      │
  NEXUS Project
```

**Nodes:** Notes, Concepts, Resources, Projects, Tasks.
**Edges:** References, Related, Uses, Requires, Explains.

Use an appropriate graph visualization library if one is already available. If not, use a suitable
lightweight library. **The graph must remain usable with many nodes.** Include zoom, pan, node
selection, relationship display, filtering, focus on selected node.
**Do not create a graph that looks impressive but becomes unusable.**

### KNOWLEDGE NODE PAGE

When opening a concept/note, show: Title, Description, Tags, Related knowledge, Backlinks,
Connected projects, Connected tasks, Resources, Revision history.
**This should feel like a complete knowledge object.**

### LINK KNOWLEDGE TO PROJECTS

```text
Project: NEXUS
Knowledge: React Architecture, FastAPI, PostgreSQL, JWT, Machine Learning
```

Project pages should be able to display related knowledge.

### LINK KNOWLEDGE TO TASKS

```text
Task: Implement AVL Tree
Related knowledge: AVL Trees, Tree Rotations, Balance Factor
```

This relationship will later help NEXUS understand what knowledge is required for tasks.

### KNOWLEDGE → LEARNING FOUNDATION

**Do NOT build the complete Learning Intelligence system yet.** But create clean relationships that
allow future: Knowledge → Learning Topic → Study Session → Performance.
**Do not duplicate the same entities unnecessarily.**

### IMPORT / EXPORT

Implement basic knowledge export. Allow exporting a note as Markdown, Plain text. If practical,
allow JSON export for a complete knowledge object. **Do not implement cloud synchronization.**

### SEARCH

Implement knowledge-specific search. Search note titles, note content, concepts, resources,
bookmarks, tags. Use database-supported search where appropriate. **Do not build the final global
search system yet. Phase 13 will create the unified global search.**

### FILTERING

Allow filtering by Tags, Category, Type, Created date, Updated date, Status. Provide useful
sorting.

### KNOWLEDGE DASHBOARD

Show Total Notes, Concepts, Resources, Bookmarks, Recent Knowledge, Most Used Tags, Recently
Updated, Connected Projects. **Do NOT manufacture analytics. All numbers must come from real
database data.**

### UI/UX — VERY IMPORTANT

This is a major visual phase. Pay particular attention to: note editor, note list, knowledge
object page, graph view, search, tag management, backlinks, revision history.

Use excellent typography, comfortable reading width, clean editor layout, subtle transitions,
polished dialogs, command-style actions where useful, skeleton loading, empty states, error
states, toast feedback, keyboard-friendly interactions.

**Avoid:** generic CRUD tables everywhere, giant unnecessary cards, excessive colors, excessive
gradients, cluttered graph UI. **For reading pages, typography and whitespace are extremely
important.**

### KEYBOARD SHORTCUTS

If practical, implement useful shortcuts. Examples: `Ctrl/Cmd + K` (global/search command
interface foundation), `Ctrl/Cmd + S` (save note), `N` (new note where appropriate).
**Do not create shortcuts that interfere with browser behavior. Display shortcut hints where
useful.**

### AUTOSAVE UX

The editor should clearly communicate: `Saving...`, `Saved just now`, `Last saved 20 seconds
ago`, `Unable to save`. **Never silently lose user content.**

### OFFLINE / LOCAL-FIRST CONSIDERATIONS

Because NEXUS runs locally, design the editor so temporary network/API failures do not immediately
destroy user work. If practical, maintain temporary unsaved editor state in browser
memory/local storage. **Do not build a full offline synchronization engine yet.**

### DATABASE

Create appropriate models for: `notes`, `note_revisions`, `tags`, `categories`, `bookmarks`,
`resources`, `documents`, `concepts`, `knowledge_relationships`.

**Every knowledge object must belong to a user unless explicitly designed otherwise. Prevent
cross-user access.**

### AUTHORIZATION

Users must only access their own: Notes, Bookmarks, Resources, Concepts, Tags, Categories,
Documents, Relationships. **Do not trust IDs supplied by the frontend. Always validate ownership
server-side.**

### API

```text
GET    /api/v1/knowledge/notes
POST   /api/v1/knowledge/notes
GET    /api/v1/knowledge/notes/{id}
PATCH  /api/v1/knowledge/notes/{id}
DELETE /api/v1/knowledge/notes/{id}

GET    /api/v1/knowledge/concepts
POST   /api/v1/knowledge/concepts

GET    /api/v1/knowledge/resources
POST   /api/v1/knowledge/resources

GET    /api/v1/knowledge/bookmarks
POST   /api/v1/knowledge/bookmarks

GET    /api/v1/knowledge/tags
POST   /api/v1/knowledge/tags

GET    /api/v1/knowledge/graph

GET    /api/v1/knowledge/search

GET    /api/v1/knowledge/notes/{id}/revisions
```

Adapt naming to the existing backend architecture. **Use pagination for large collections.**

### EVENT TRACKING

Record useful events for future analytics and ML.

```text
NOTE_CREATED    NOTE_UPDATED    NOTE_VIEWED    NOTE_ARCHIVED
CONCEPT_CREATED    RESOURCE_CREATED    BOOKMARK_CREATED
KNOWLEDGE_LINK_CREATED    KNOWLEDGE_LINK_REMOVED
NOTE_REVISION_CREATED
```

These events will eventually help NEXUS understand: what knowledge the user works with, what they
revisit, what concepts connect to projects, what knowledge is relevant to tasks.

### FUTURE AI/ML PREPARATION

**Do NOT train models in Phase 5.** But ensure the collected data can later support: knowledge
recommendation, related concept prediction, resource recommendation, learning recommendations,
semantic search, personalized AI responses.

Capture useful structured information. **Do not collect unnecessary personal information.**

### PERFORMANCE

**Do not load an entire knowledge graph immediately if the user has thousands of nodes.** Use
pagination, lazy loading, filtered graph queries, limited initial graph size. **Do not perform
expensive recursive database queries for every page load.**

### TESTING

Note creation, note editing, note deletion, note ownership, autosave behavior, revision creation,
tag creation, duplicate tag handling, bookmark creation, resource creation, concept creation,
knowledge relationships, backlinks, graph retrieval, search, authorization.

**Test that User A cannot access User B's knowledge.** Test important relationship constraints.

### 🚨 MANDATORY FINAL REGRESSION CHECK

After Phase 5 implementation, **STOP.** Run a complete regression check.

**Frontend:** TypeScript, build, routes, console errors, dashboard, projects, tasks, planner,
calendar, knowledge pages, editor, graph, responsive behavior, dark/light mode.
**Backend:** startup, imports, knowledge APIs, authentication, authorization, validation, exception
handling.
**Database:** migrations, foreign keys, relationships, indexes, constraints.
**Integration:** frontend → backend, authentication, project ↔ knowledge, task ↔ knowledge,
search, autosave, revisions.

Run ALL existing tests from previous phases. If any regression is discovered, **FIX IT.** Then
re-run the affected tests. **Do not simply report fixable bugs as known issues.**

### QUALITY BAR

Before completion ask: "Does this feel like a real knowledge-management product?" If not, improve
the UI/UX. **The Knowledge Base should be one of the most polished sections of NEXUS.**

### DO NOT IMPLEMENT PHASE 6

**Do NOT build the full Analytics Engine yet. Stop after Phase 5 is implemented, tested and
stable.**