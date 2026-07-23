---
status: done
created: 2026-07-22
last_updated: 2026-07-22
scope: step-06
depends_on:
  - .agents/plans/01-architecture-foundation.md
  - .agents/plans/02-directory-and-class-architecture.md
---

# Step 06 - Agent Usage Guidance Distribution

## Summary

The MCP tools exist and carry strong per-tool descriptions, but nothing
teaches agents the **workflow-level** contract: search before answering,
remember when learning, reinforce/forget after use, and the shared scope
conventions. This step distributes that guidance through two channels
(decision: phương án C, 2026-07-22):

1. **Server `instructions`** (protocol layer): a 3-4 line version of the
   recall loop, sent at MCP handshake. Free, travels with the server, but
   surfacing depends on the host — so it is never the only channel.
2. **Canonical skill shipped in this repo** (host layer): the full guidance
   as a standard Agent Skills package at `skills/brain-memory/SKILL.md`,
   installable into ~70 harnesses via the ecosystem installer
   (`npx skills add`), with documented manual paths as fallback.

Non-goal: building our own per-harness installer. Web research (2026-07-22)
confirmed the ecosystem already solved harness-directory discovery —
`vercel-labs/skills` maintains the agent→path table and installs by symlink.
We ship a standard-layout skill; installers do the rest.

Key design decisions:

- **Scope conventions are a product default**, not deployment policy: unify
  is the product goal, and a convention only unifies when identical
  everywhere. `project` = repo slug (default for work context), `user` =
  user handle, `global` = cross-everything knowledge.
- **One canonical text, three lengths**: `skills/brain-memory/SKILL.md` is
  the full contract; `_SERVER_INSTRUCTIONS` is its 3-4 line summary; docs
  link to them instead of copying the guidance.
- The skill must tell agents to **stop and report** when `brain_*` tools are
  absent (MCP not registered for that client) instead of emulating memory in
  local files.

Success criteria:

- `npx skills add <this-repo>` (or local path) discovers and installs
  `brain-memory` into at least claude-code/codex/gemini directory
  conventions.
- A fresh agent session with the MCP registered recalls/stores/closes the
  loop following only the skill + tool descriptions.
- `brain_health`-style docs let a user register the MCP and install the
  skill in under 5 minutes.

## Tasks

### GOAL-001: Canonical skill package in repo

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Create `skills/brain-memory/SKILL.md`: frontmatter (`name`, `description` with activation triggers — resuming work, recalling past decisions, learning something worth remembering; include the literal `brain_*` tool names for matching), body sections: when to recall (search-before-answer, brain_recent on resume), when to remember (decision/bug/preference/fact/task catalogs, diary-style topic+summary, honest importance), scope conventions (product default above), close-the-loop (get → reinforce/forget), rules (pure reads, no secrets, no identity inputs, report missing tools instead of emulating). English, matching the format of existing curated skills. | ✅ | 2026-07-22 |
| TASK-002 | Verify standard-layout compatibility: `npx skills add . --list` (or `--list` against the repo path) from repo root shows `brain-memory`; install into one local harness dir via symlink and confirm the file resolves. | ✅ | 2026-07-22 |

### GOAL-002: Server instructions carry the short loop

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-003 | Rewrite `_SERVER_INSTRUCTIONS` in `src/app.py` to a compact version of the loop: shared memory for all agents; search before answering, remember decisions/fixes/preferences as diary entries (topic + summary); after using a memory, brain_reinforce if correct / brain_forget if wrong; scope=project with the repo slug by default. | ✅ | 2026-07-22 |
| TASK-004 | Extend a unit test (e.g. `tests/unit/test_tools.py` or a new server-assembly assertion) to check the built FastMCP server's instructions mention the loop verbs (`brain_reinforce`, `brain_forget`) so the short contract cannot silently rot. | ✅ | 2026-07-22 |

### GOAL-003: Docs: registration + installation runbook

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | `docs/deployment.md`: add "Connect agents" section — MCP client registration (stdio `.mcp.json` snippet; note `AGENT_ID` should differ per host for provenance), skill install (`npx skills add Flowerf19/another-brain` primary; manual copy paths `~/.claude/skills/`, `~/.codex/skills/`, `~/.gemini/skills/` as fallback; note `.agents/skills/` shared convention). | ✅ | 2026-07-22 |
| TASK-006 | Root `README.md`: one short section linking to the deployment runbook (no duplicated guidance text). | ✅ | 2026-07-22 |
| TASK-007 | `.agents/PROJECT_CONTEXT.md`: record `skills/` in runtime state and the two-channel guidance decision (one line, pointing here). | ✅ | 2026-07-22 |

## Test Plan

- `npx skills add . --list` from the repo root lists `brain-memory` (layout
  contract). Requires Node/network; if unavailable, verify manually that
  `skills/brain-memory/SKILL.md` parses as YAML frontmatter + markdown.
- `uv run pytest tests/unit` — instructions assertion from TASK-004 passes.
- Manual acceptance (one host): register the MCP, install the skill, start a
  fresh session, ask a question answerable from a stored memory — the agent
  should call `brain_search` unprompted.

## Assumptions

- Phương án C approved 2026-07-22: two channels (server instructions +
  shipped skill), no custom installer.
- Scope conventions ship as product defaults inside the skill; deployments
  may extend but the shipped text is the shared contract.
- `npx skills add` resolves a plain GitHub repo containing `skills/<name>/`
  — verified against vercel-labs/skills README (source formats + layout);
  TASK-002 re-verifies locally before docs recommend it.
- The npm launcher (not yet implemented) will later offer the same
  install command; no launcher work in this step.
- The skill text lives only in this repo; the user's personal
  `~/.claude/skills` clone consumes it via the installer, not by
  duplicating the file.
