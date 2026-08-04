# MCP Tools

The server exposes eight stable tools. Self-contained schemas and server
instructions land with the MCP implementation (GOAL-013); this page is
rewritten from the final tool definitions in TASK-088.

| Tool | Purpose |
|------|---------|
| `brain_remember` | append a diary entry: stable reusable **topic** (3–8 Harrier tokens, hard max 12), `catalog`, summary, optional content |
| `brain_search` | hybrid FTS5 + exact-vector search, fused with RRF |
| `brain_recent` | newest-first timeline listing within a scope |
| `brain_get` | fetch one entry by `memory_id` (bound-brain isolated) |
| `brain_reinforce` | re-arm expiry from importance |
| `brain_forget` | soft delete with a 30-day grace window |
| `brain_health` | schema/profile/model/integrity status |
| `brain_audit` | structural mutation events for a day (no memory text) |

Contract notes: `brain_id` is process-bound and `agent_id` comes from the MCP
handshake — neither is a tool argument. Search returns previews; `brain_get`
returns detail. Memories are claims, not facts — see `memory-trust-model.md`.

## Topic guidance (`brain_remember`)

Topics are stable reusable subjects, not per-entry titles:

- target **3–8 Harrier tokens**, hard max **12** — over-limit input is
  rejected with actual/allowed counts (see the token budget validator),
  never truncated;
- reuse one topic across every entry it labels; do not restate or
  date-stamp it per entry;
- keep taxonomy in `catalog` — do not duplicate catalog values into the
  topic;
- no workflow labels ("TODO", "done", "fix later"), no keyword stuffing;
  a good topic lets `brain_search` find the *subject*, not the incident.
