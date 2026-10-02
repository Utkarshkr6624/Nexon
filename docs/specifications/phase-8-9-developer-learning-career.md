# Phases 8 + 9 — Developer Intelligence + Learning & Career Intelligence

> **Status: 🔴 Not started.** Issued as a single combined specification.
> Phase 8 requires Phase 6 (activity events). Phase 9 requires Phases 3, 4, 5, 6 and 8.

---

# Global Rules — Apply to Both Phases

## 1. Inspect Before Changing Anything

Before writing code: inspect the entire existing repository; understand the current architecture;
inspect frontend, backend, database models, migrations, API routes, services, authentication,
projects, tasks, planner/calendar, knowledge base, analytics, risk engine, recommendation engine.
Identify what is already implemented.

**Do NOT recreate existing functionality. Do NOT break existing APIs or database relationships.**

## 2. Mandatory Bug Check — Before Each Phase

Before beginning Phase 8: run whatever existing tests/checks are actually available; inspect the
application for broken routes, broken imports, TypeScript errors, Python errors, API
inconsistencies, database issues, authentication regressions, UI rendering problems, broken
navigation, broken existing features. **Fix existing bugs BEFORE adding Phase 8 features.**

Then repeat the same process before starting Phase 9.

**Do not merely report bugs. Fix them whenever possible.**

---

# PHASE 8 — DEVELOPER INTELLIGENCE

Build a complete local developer intelligence system inside NEXUS. The system must work with
**local Git repositories. Do NOT depend on GitHub APIs or paid services.**

## 8.1 Local Repository Management

Create a repository management system allowing the user to register local Git repositories.

Repository entity: `id`, `name`, `local path`, `description`, `primary language`, project
association, active/inactive, `created_at`, `updated_at`, `last_scanned_at`.

Allow adding, editing metadata, removing, rescanning, viewing details. **Validate that paths are
legitimate Git repositories where possible. Never expose arbitrary filesystem access through
unsafe API endpoints.**

## 8.2 Git Analysis Engine

Create a backend Git analysis service. **Prefer the system Git CLI or a safe Git library where
appropriate.**

### Repository information
repository name, current branch, default/current branch, number of branches, number of commits,
repository age, first commit date, latest commit date, working tree status.

### Commit information
commit hash, timestamp, message, author, additions, deletions, files changed, branch where
available.

### Activity
commits per day / week / month, active days, longest inactive period, recent activity, commit
frequency trends.

### Code statistics
Where reliably measurable: file count, language distribution, lines added, lines deleted, files
changed, source-code file counts.

**Do NOT make unsupported claims such as "You were productive for 6 hours." Git data alone cannot
prove actual working time.** Instead use factual language such as "18 commits were recorded this
week."

## 8.3 Developer Intelligence Metrics

Create deterministic metrics based on actual repository data: Commit Activity, Repository
Activity, Change Volume, Active Days, Consistency, Repository Growth, Maintenance Activity, Recent
Momentum.

**Every metric must have: numerical value, calculation definition, time range, source data,
explanation. Avoid fake precision.**

## 8.4 Developer Events

Integrate developer activity into the existing NEXUS event system.

```text
REPOSITORY_REGISTERED   REPOSITORY_SCANNED   COMMIT_DETECTED
BRANCH_CREATED          BRANCH_CHANGED      FILE_ACTIVITY_DETECTED
REPOSITORY_UPDATED
```

**These events must be usable later by the analytics and ML systems.**

## 8.5 Project Integration

Allow repositories to be associated with NEXUS projects.

```text
Project: CampusPulse   Repository: campuspulse
```

Then project analytics can show: project tasks, project deadlines, repository activity, commit
activity, recent code changes.

**Do not claim that Git commits equal task completion. Keep these as separate measurable
signals.**

## 8.6 Developer Dashboard

### Overview
total repositories, commits, active days, recent activity, files changed, lines added/deleted.

### Activity chart
daily commits, weekly commits, monthly activity.

### Repository cards
name, language, current branch, recent commits, last scan, activity level, project association.

### Commit timeline
timestamp, message, repository, branch, author, change statistics.

### Repository detail page
repository summary, activity chart, commit history, branch information, language statistics,
change statistics, project connection.

**Use proper loading, empty, error and stale-data states.**

## 8.7 Git Scan Performance

**Do not rescan repositories unnecessarily.** Implement manual scan, scan timestamps,
incremental analysis where practical, safe timeouts, error handling.
**A broken repository must not crash the entire NEXUS application.** Show useful errors such as
"Repository could not be scanned." instead of exposing raw stack traces to users.

## 8.8 Future ML Preparation

Prepare developer data for future ML without actually training models. Potential features:
`commits_last_7d`, `commits_last_30d`, `active_days_7d`, `active_days_30d`, `files_changed_7d`,
`additions_7d`, `deletions_7d`, `repository_age`, `inactivity_days`, `commit_frequency`,
`project_association`.

Store these in a clean, versionable structure. **Do NOT build the actual ML models in Phase 8.**

## Phase 8 UI/UX Requirements

Make the Developer section feel like a professional developer analytics product: polished cards,
responsive layouts, charts, hover states, tooltips, skeleton loading, empty states, error states,
command-friendly navigation, filters, date ranges, smooth transitions, consistent typography,
dark/light mode, responsive mobile/tablet/desktop layouts.

**Use the existing NEXUS design system instead of creating a completely different visual style.**

## Phase 8 Testing

Repository registration, repository validation, Git scanning, commit parsing, statistics, date
filtering, activity calculations, project association, invalid repository handling, event
creation, ownership/security.

Test edge cases: empty repository, repository with one commit, huge commit history, detached HEAD,
missing branch, deleted repository path, repository with no recent activity.

## Phase 8 Final Regression Check

Run all available tests, frontend checks, backend checks. Check API routes, authentication,
Projects, Tasks, Planner, Knowledge, Analytics, Risk/Recommendations, Developer Intelligence.
**Fix regressions before moving to Phase 9.**

**If Docker/PostgreSQL cannot be executed in the current environment, do NOT falsely claim they
passed. Clearly document what was verified and what could not be executed.**

---

# PHASE 9 — LEARNING + CAREER INTELLIGENCE

After Phase 8 passes its regression check, implement Phase 9. This phase creates a structured
system for learning progress and career development. It must integrate with: Tasks, Projects,
Knowledge Base, Analytics, Developer Intelligence, Risk/Recommendation Engine.

## 9.1 Learning Goals

Fields: title, description, target skill/topic, target date, priority, status, progress, estimated
effort, project relationship, knowledge relationship, `created_at`, `updated_at`.

**Statuses:** `NOT_STARTED`, `IN_PROGRESS`, `PAUSED`, `COMPLETED`, `ARCHIVED`.

## 9.2 Skills System

Examples: Python, Java, React, FastAPI, Machine Learning, Data Structures, SQL, Git.

Each skill can have: name, category, description, current level, target level, confidence,
evidence count, last activity, related projects, related tasks, related knowledge.

**Do not pretend the system objectively knows someone's skill level. Skill levels must be
user-defined OR clearly labelled as system-derived estimates based on measurable evidence.**

## 9.3 Learning Activities

Track learning activities: study sessions, completed learning tasks, notes created, resources
viewed, concepts learned, projects completed, coding activity. Connect them to skills where
appropriate.

```text
A Java task can contribute evidence to Java activity.
A Machine Learning note can contribute evidence to ML learning activity.
```

**Keep the evidence traceable.**

## 9.4 Learning Dashboard

Active learning goals, completed goals, progress, learning streak/activity, recent learning
sessions, skills, skill activity, upcoming learning deadlines, learning workload, knowledge
growth.

Charts: learning activity over time, progress by goal, activity by skill, study/session
distribution.

**Do not fabricate data. If insufficient data exists, clearly show "Not enough data yet."**

## 9.5 Skill Gap System

Allow users to define target skills.

```text
Target: Machine Learning Engineer
Required skills: Python, Statistics, SQL, Machine Learning,
                 Deep Learning, Git, Deployment
```

NEXUS can compare: user-defined current skill level, user-defined target level, measurable evidence
from NEXUS. Then show a transparent gap.

```text
Machine Learning — Target: 4 / Current: 2
```

**The system must explain the evidence.**

> **Do NOT say** "You are not good at Machine Learning."
> **Instead say** "Your current self-assessed level is 2/5. NEXUS recorded 6 related learning
> activities in the last 30 days."

## 9.6 Career Profile

Support: target role, target domain, skills, projects, achievements, certifications, education,
experience, portfolio links, Git repositories.

**Keep this user-controlled. The system should not invent qualifications.**

## 9.7 Career Evidence

Examples: project completed, feature shipped, repository activity, technical skill activity,
learning milestone, certification, achievement.

Each evidence item includes: type, title, description, date, related project, related skill,
source. **This will later support resume/career intelligence.**

## 9.8 Career Dashboard

### Career profile
target role, target domain, skills, projects, achievements.

### Skill overview
current level, target level, evidence, recent activity.

### Portfolio evidence
projects, repositories, accomplishments.

### Development areas
areas where the user has explicitly defined a target but has limited evidence. **Use neutral
language.**

## 9.9 Learning Recommendations

Integrate Phase 9 with the existing deterministic Recommendation Engine. Generate explainable
recommendations such as:

> "You have a Machine Learning goal with a deadline in 14 days and only 35% recorded progress.
> Consider scheduling three learning sessions this week."

> "Python has been inactive for 21 days while it is marked as a target skill. Consider adding a
> short practice session."

**Every recommendation must explain what, why, evidence, suggested action. Do not use ML yet.
Do NOT build the Phase 10 ML system here.**

## 9.10 Learning/Career Events

```text
LEARNING_GOAL_CREATED    LEARNING_GOAL_UPDATED    LEARNING_GOAL_COMPLETED
LEARNING_SESSION_RECORDED
SKILL_CREATED   SKILL_UPDATED   SKILL_ACTIVITY_RECORDED
CAREER_PROFILE_UPDATED    CAREER_EVIDENCE_ADDED    CAREER_EVIDENCE_UPDATED
```

**These must feed the analytics/event system.**

## 9.11 ML-Ready Data Preparation

Phase 9 should prepare clean data for Phase 10.

### Learning
`sessions_last_7d`, `sessions_last_30d`, `learning_minutes`, `goal_progress`,
`goal_deadline_distance`, `completion_rate`, `learning_consistency`,
`skill_activity_frequency`.

### Career
`projects_completed`, `project_activity`, `repositories`, `relevant_skill_evidence`,
`learning_activity`, `portfolio_evidence_count`.

Store these in a structured way where appropriate. **DO NOT TRAIN MODELS IN PHASE 9. Phase 10
will be the actual ML training pipeline.**

## Phase 9 UI/UX

Make the Learning and Career sections feel like premium professional software: polished
dashboards, progress indicators, charts, skill cards, timeline components, filters,
modals/drawers, inline editing, responsive design, dark/light mode, tooltips, animations,
skeleton loaders, empty states, error states, accessible forms, keyboard navigation.

**Everything must visually match the existing NEXUS application. Do not create generic ugly CRUD
screens.**

## Phase 9 Security

All user-owned data must have server-side ownership checks. Users must never be able to access
another user's learning goals, skills, career profile, evidence or learning activities.
**Do not trust frontend user IDs. Validate authorization on the backend.**

## Phase 9 Testing

Learning goals, skills, skill evidence, learning activities, career profile, career evidence,
skill gap calculations, learning metrics, recommendation generation, ownership/security, event
creation.

Test edge cases: zero learning activity, no skills, no career profile, completed goal, overdue
learning goal, missing target skill, incomplete data, duplicate evidence, deleted related
project/task.

---

# Final Full-System Regression Check

After Phase 9 is implemented, perform a full regression audit of the ENTIRE NEXUS system.

**Core:** authentication, authorization, user settings, navigation, themes.
**Work management:** projects, tasks, subtasks, dependencies, statuses, priorities.
**Planning:** calendar, scheduling, work sessions, conflicts.
**Knowledge:** notes, resources, concepts, backlinks, knowledge graph.
**Intelligence:** analytics, metrics, risks, recommendations.
**Developer:** repositories, Git scanning, commit analytics.
**Learning:** learning goals, skills, learning activities.
**Career:** career profile, evidence, skill gaps.

**Fix any regressions you discover. Do not merely list them.**

# Data Integrity

Verify migrations are correct, foreign keys are correct, indexes exist where needed, timestamps
are consistent, timezone handling is safe, user ownership is enforced, cascade behaviour is
intentional, duplicate records are prevented where appropriate. **Do not destroy existing user
data.**

# Performance

Check for N+1 queries, unnecessarily expensive Git scans, unbounded API responses, missing
pagination, excessive frontend requests, unnecessary re-renders, expensive analytics
calculations. **Add reasonable indexes and caching where justified. Do not prematurely
over-engineer.**

# Documentation

Update: README, architecture documentation, API documentation where needed, environment
variables, setup instructions, Git repository scanning instructions, learning/career feature
documentation, known limitations.

**Clearly distinguish: implemented, tested, unable to verify due to environment limitations.
Never claim something passed if it was not actually executed.**

---

# 🚨 VERY IMPORTANT: PHASE 10 BOUNDARY

After Phase 9 is complete, **STOP.**

**Do NOT:** train ML models · install Kaggle API · download Kaggle datasets · build MLflow · build
model registry · create prediction models · implement ML inference · implement Ollama · implement
voice assistant.

Those belong to later phases. **The next step after Phase 9 will be setting up the Kaggle API/
dataset pipeline and actual ML training strategy before beginning Phase 10.**