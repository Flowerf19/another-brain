# Architecture

Canonical sources (read those for design rationale; this file is the map):

- [`.agents/plans/another-brain-architecture.md`](../.agents/plans/another-brain-architecture.md)
  — product/technical source of truth
- `.agents/plans/01`–`04` — approved step contracts (foundation, directory/class
  architecture, model install policy, memory record + Redis index contract)
- `.agents/plans/05-redis-hybrid-search.md` — `FT.HYBRID` mechanism explainer

## Shape

MCP host → transport (stdio / Streamable HTTP) → `MemoryService` → Redis 8.8.

- One Redis HASH per memory under `ab:memory:{brain_id}:{memory_id}`, packed
  FLOAT32 embedding (640-dim Harrier) in the same HASH, TTL from importance.
- One global RediSearch index `ab:idx:memory` (`PREFIX ab:memory:`): TAG
  (`brain_id`, `scope`, `scope_id`, `topic`, `catalog`, `timeline_day`), TEXT
  NOSTEM (`summary`, `content`), NUMERIC SORTABLE (`importance`,
  `period_start`, `period_end`, `created_at`), NUMERIC `deleted_at`, VECTOR
  HNSW `embedding` (FLOAT32, COSINE).
- Search is one `FT.HYBRID` call (BM25 + KNN + RRF in Redis), then an
  app-layer cosine floor, gate-before-limit.
- Soft delete via `deleted_at`, excluded at index level; audit trail in
  per-brain-per-day HASHes `ab:audit:{brain_id}:{YYYY-MM-DD}`.
- No auth layer: `brain_id`/`agent_id` bound from config.

## Module map (`src/`)

| Module | Role |
| --- | --- |
| `main.py` | CLI: `serve`, `model plan/pull/status`, `admin restore/hard-delete` |
| `app.py` | composition root: config → installer → embedder → index → repo → engine → service → tools |
| `config.py` | env validation; every inconsistency is a startup `ConfigError` |
| `server/` | FastMCP surface (`tools.py`), stdio/HTTP transports |
| `memory/` | domain (`models.py`), use cases (`service.py`), `FT.HYBRID` orchestration + cosine gate (`search.py`), local embedding provider (`embeddings.py`), TTL table (`retention.py`) |
| `storage/` | key builder, index manager (create/verify/meta), Redis repository + mapper |
| `models/` | local model install policy, registry, cache, installer, runtime profile |
| `audit/` | secret-free mutation events |

Not yet implemented: `server/resources.py`, `storage/migrations.py`,
`memory/normalization.py`, external embedding providers (only `local` works),
`packages/npm-launcher`, service Dockerfile.
