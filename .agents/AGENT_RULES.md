# Agent Rules

## Source Of Truth

- Treat `.agents/plans/another-brain-architecture.md` as the current
  architecture plan.
- Keep root `README.md` linked to that plan and suitable for GitHub readers.
- Keep `.agents/` docs aligned with real files and commands as the repo grows.
- This repo is independent; do not import March7/Evernight runtime code.

## Memory Contract

- Store canonical memory text in the memory's natural language by default.
- Record `canonical_language`; keep `original_content` and `original_language`
  when normalization changes the source text or audit/debug needs it.
- Embed canonical `content`, not an optional translated/debug copy.
- Do not force English translation unless the configured memory policy says so.
- Preserve exact names, ids, paths, commands, dates, numbers, and quoted user
  preferences during normalization.
- Keep the canonical storage model as timeline memory.
- Do not replace topic timeline chunks with arbitrary token chunks without an
  explicit architecture change.

## Identity And Auth

- `brain_id` is the storage isolation boundary.
- `agent_id` is provenance and permission context, not the default memory
  namespace.
- The server should derive trusted `brain_id` and `agent_id` from config or auth
  context whenever possible.
- Do not trust an LLM-supplied `agent_id` for authorization.
- Never run storage queries without a `brain_id` filter.

## Storage Rules

- Redis Stack is the expected MVP backend until the architecture changes.
- Treat Redis Stack as the source of truth for memory records, vector storage,
  full-text index, vector index, and TTL retention.
- Store memory records as Redis HASH documents with packed FLOAT32 embedding
  bytes in the HASH.
- Run both vector KNN and BM25 lexical search through RediSearch on Redis.
- Apply per-memory Redis TTL from importance and refresh TTL when a merge updates
  a memory.
- Index schema changes must include migration/reindex notes.
- Embedding dimension changes require explicit reindex handling.
- Soft delete is the default delete behavior. Hard delete should be admin-only.
- Health/status output must not reveal secrets.

## MCP Rules

- Keep tool names short and stable, using the `brain_*` prefix.
- Initial tools should match the plan: `brain_remember`, `brain_search`,
  `brain_recent`, `brain_get`, `brain_forget`, `brain_health`.
- Tool responses should provide evidence needed for relevance judgment:
  `memory_id`, canonical content/summary, kind, subject, scope, source,
  `agent_id`, time, importance, and relevance score.
- MCP resources should expose machine-readable state, not agent-specific
  behavior.

## Packaging Rules

- Docker is the real deployment target for the service and persistent storage.
- npm should be a launcher/adapter for MCP clients, not a second implementation
  of the memory engine.
- Avoid adding package managers or frameworks until the first implementation
  plan chooses the runtime stack.

## Documentation Rules

- If source paths, commands, env vars, or service names are added, update
  `.agents/PROJECT_CONTEXT.md` and `.agents/TESTING_GUIDE.md`.
- If chunking, identity, or language policy changes, update
  `.agents/plans/another-brain-architecture.md` first, keep root `README.md`
  linked, and then mirror the operational consequences here.
