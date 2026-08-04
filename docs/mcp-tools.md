# MCP Tools

The server exposes eight stable tools. Self-contained schemas and server
instructions land with the MCP implementation (GOAL-013); this page is
rewritten from the final tool definitions in TASK-088.

| Tool | Purpose |
|------|---------|
| `brain_remember` | append a diary entry (topic, catalog, summary, optional content) |
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
