# Agent Rules

## Source Of Truth

- Treat `.agents/plans/another-brain-architecture.md` as the current
  architecture plan.
- Keep root `README.md` linked to that plan and suitable for GitHub readers.
- Keep `.agents/` docs aligned with real files and commands as the repo grows.
- This repo is independent; do not import March7/Evernight runtime code.

## Memory Contract

- Diary model (Step 04): one memory = `timeline_day` + `topic` slug + 1-2
  sentence `summary`, classified by an open-vocabulary `catalog`, with
  optional `content` detail/checklist (max `CONTENT_MAX_CHARS`).
- `summary` is the canonical text: it is what gets embedded and previewed in
  search. `content` is BM25-searchable but never embedded.
- Append-only: no merge, no update tool. An update is a new `brain_remember`
  plus a `brain_forget` on the old entry.
- Keep memory text in its natural language; TEXT indexes use NOSTEM because
  the corpus is multilingual. Translation-pipeline fields were deliberately
  cut from the MVP schema (Step 04, decision 5).
- Preserve exact names, ids, paths, commands, dates, and numbers in
  summaries.
- Keep the canonical storage model as timeline memory. Do not replace topic
  timeline entries with arbitrary token chunks without an explicit
  architecture change.

## Identity

- `brain_id` is the storage isolation boundary.
- `agent_id` is provenance, not the default memory namespace.
- There is no auth or permission layer: the service is one shared brain for a
  set of trusted agents — unifying knowledge across them is the product goal.
  Do not expose the HTTP transport on an untrusted network.
- The server binds `brain_id` from config and detects `agent_id` per session
  from the MCP handshake; tool inputs never carry identity, so an LLM cannot
  declare its own.
- Never run storage queries without a `brain_id` filter.

## Storage Rules

- Redis 8.8 (bundled Query Engine; >= 8.4 required for `FT.HYBRID`) is the
  only backend until the architecture changes. It owns memory records, vector
  storage, full-text index, vector index, and TTL retention.
- Store memory records as Redis HASH documents with packed FLOAT32 embedding
  bytes in the HASH.
- Hybrid search runs as one `FT.HYBRID` call (BM25 + KNN + RRF fused in
  Redis); the app layer applies the cosine floor before the top-k limit.
- Apply per-memory Redis TTL from importance. The only renewal is explicit
  `brain_reinforce`; no read path ever refreshes TTL.
- Index schema changes must include migration/reindex notes.
- Embedding dimension changes require explicit reindex handling; the server
  refuses to start on a DIM mismatch.
- Soft delete is the default delete behavior (index-level exclusion via
  `deleted_at`). Hard delete and restore are admin-only CLI operations.
- Health/status and audit output must not reveal secrets or memory text.

## MCP Rules

- Keep tool names short and stable, using the `brain_*` prefix.
- Tool surface (implemented): `brain_remember`, `brain_search`,
  `brain_recent`, `brain_get`, `brain_reinforce`, `brain_forget`,
  `brain_health`, `brain_audit`.
- Search/recent return previews only (`memory_id`, `topic`, `catalog`,
  `summary`, `timeline_day`, `importance`, `has_content`, relevance evidence);
  `content` comes from `brain_get`. Never return embeddings.
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
