# MCP Tools

The server exposes eight stable tools. Self-contained schemas and server
instructions landed with the MCP implementation (GOAL-013); this page is
refreshed from the final tool definitions in `another_brain/mcp/tools.py`.

| Tool | Purpose |
|------|---------|
| `remember` | append a diary entry: stable reusable **topic** (3–8 Harrier tokens, hard max 12), `catalog`, summary, optional content |
| `search` | hybrid FTS5 + exact-vector search, fused with RRF |
| `recent` | newest-first timeline listing within the bound brain |
| `get` | fetch one entry by `memory_id` (bound-brain isolated) |
| `reinforce` | re-arm expiry from importance |
| `forget` | soft delete with a 30-day grace window |
| `health` | schema/profile/model/integrity status |
| `audit` | structural mutation events for a day (no memory text) |

Contract notes: `brain_id` is process-bound and `agent_id` comes from the MCP
handshake — neither is a tool argument. Search returns previews; `get`
returns detail. Memories are claims, not facts — see `memory-trust-model.md`.

## Topic guidance (`remember`)

Topics are stable reusable subjects, not per-entry titles:

- target **3–8 Harrier tokens**, hard max **12** — over-limit input is
  rejected with actual/allowed counts (see the token budget validator),
  never truncated;
- reuse one topic across every entry it labels; do not restate or
  date-stamp it per entry;
- keep taxonomy in `catalog` — do not duplicate catalog values into the
  topic;
- no workflow labels ("TODO", "done", "fix later"), no keyword stuffing;
  a good topic lets `search` find the *subject*, not the incident.
