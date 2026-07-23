---
name: brain-memory
description: Use the another-brain MCP tools (brain_remember, brain_search, brain_recent, brain_get, brain_reinforce, brain_forget) as shared long-term memory. Activates at the start of a task that may continue previous work, when recalling past decisions/fixes/preferences, when learning something worth remembering across sessions or agents, or after using a recalled memory.
argument-hint: Recall, store, or maintain long-term memories via the brain_* tools.
---

Another Brain is the shared long-term memory for all agents of this
deployment. Whatever one agent stores, every other agent on the same
`brain_id` can recall. Use it so the next agent — or your next session — can
continue work without re-deriving context. The tools appear as `brain_*` MCP
tools; if they are absent, the server is not registered for this client —
stop and say so instead of emulating memory in local files.

## When to recall

- **Starting a task**: `brain_search` before answering anything the user may
  have told an agent before — prior decisions, fixed bugs, preferences,
  project conventions. Do not re-ask what memory already knows.
- **Resuming interrupted work**: `brain_recent` (scope=project, scope_id =
  the git-root basename of the current project) to catch up on what was
  stored recently, then `brain_search` for the specific topic.
- **Tracing one thread**: filter by `topic` slug or `timeline_day`.

## When to remember

Call `brain_remember` when you learn something worth recalling in a later
session or by another agent:

- a decision and its rationale (`catalog=decision`)
- a bug and its fix (`catalog=bug`)
- a user preference (`catalog=preference`)
- a fact or convention about the project (`catalog=fact` or `note`)
- an open task or work-in-progress state (`catalog=task`)

Write it as a diary entry: `topic` = stable lowercase-kebab slug
(e.g. `redis-upgrade`, `auth-decision`), `summary` = 1-2 sentences holding
the actual knowledge (names, commands, versions, dates preserved exactly).
Put long detail or checklists in `content`. Set `importance` honestly — it
sets retention (5=365d, 3=90d, 1=7d); the default 3 is fine for most
entries. Repeats of the same knowledge are acceptable; the store is
append-only, and an update is a new `brain_remember` plus `brain_forget` on
the old entry.

## Scope conventions (shared contract — do not improvise)

- `scope=project`, `scope_id` = the project slug, derived mechanically:
  `basename "$(git rev-parse --show-toplevel)"` (e.g. `another-brain`).
  Project knowledge; the default for work context. Never invent a different
  spelling or abbreviation of the project name — two slugs for one project
  splits its memory in half.
- `scope=user`, `scope_id` = the user's handle — personal preferences and
  cross-project user facts.
- `scope=global` — knowledge useful everywhere; omit `scope_id`.

Consistent `scope_id` values are what let agents find each other's memories.
When in doubt, `brain_recent` on the project scope shows the slugs already
in use.

## Close the loop after using a memory

Search and recent return previews (summary only). If `has_content` is true
and you need the detail, pull it with `brain_get`. Then, after actually
*using* a memory:

- proved correct and valuable → `brain_reinforce` (renews its retention —
  the only way a memory lives longer)
- proved wrong or stale → `brain_forget` (soft delete; an admin can restore
  during the grace window)
- no verdict → do nothing; it expires on its own schedule

Never reinforce on sight — fetch, use, judge, then reinforce.

## Rules

- Reads (`brain_search`, `brain_recent`, `brain_get`) are pure: they change
  nothing and are always safe.
- Do not store secrets, credentials, or large blobs — store summaries of
  knowledge, not data dumps.
- Do not pass `brain_id` or `agent_id`; the server binds the brain from
  config and detects your client identity from the MCP handshake.
- If `brain_health` reports degraded, tell the user instead of working
  around a broken memory store.
