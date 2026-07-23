# Project Context

## Product Boundary

Another Brain is a standalone memory service for many agent systems. It exposes
memory over MCP and owns storage, retrieval, identity boundaries, and memory
policy.

Clients send explicit memories and ask for recall. The service must not depend
on any client agent's loop, persona, Discord integration, or project-specific
runtime.

## Canonical Architecture

The root `README.md` is the public overview. The architecture source of truth
is `.agents/plans/another-brain-architecture.md`; the approved contracts are
`.agents/plans/01`–`04` plus the `05` FT.HYBRID explainer.

Core decisions:

- MCP is the primary integration surface (stdio and Streamable HTTP, both
  implemented).
- Docker is the primary deployment shape; today compose provides Redis 8.8 and
  the server runs from source. npm is a planned convenience launcher, not a
  second memory engine.
- Redis 8.8 (>= 8.4 for `FT.HYBRID`) is the only database: memory HASH records,
  packed FLOAT32 embeddings, RediSearch index, TTL retention.
- Memory is a timeline diary: `timeline_day` + `topic` + `summary` (embedded) +
  optional `content`, open-vocabulary `catalog`. Append-only, no merge, no
  update tool.
- Retention by importance TTL (365/180/90/30/7 days); only explicit
  `brain_reinforce` renews. Reads are pure. Failure direction is forgetting.
- Search is one `FT.HYBRID` call fused in Redis, then an app-layer cosine
  floor (`SEARCH_MIN_COSINE=0.30`), gate before limit.
- Embedding: local `microsoft/harrier-oss-v1-270m` (640-dim, SentenceTransformers,
  query prompt `web_search_query`). External providers are not implemented.
- No auth layer: one shared brain for trusted agents; identity is config-bound.
  Do not expose HTTP on untrusted networks.
- Agent guidance ships two ways (step 06): a short recall loop in the MCP
  server `instructions`, and the canonical `brain-memory` skill at
  `skills/brain-memory/` (install via `npx skills add`).

## Identity Model

- `brain_id` - shared memory namespace and storage isolation boundary.
- `agent_id` - calling client/agent, recorded as provenance on writes/audit.
- `scope` - `user | project | global` (no `channel`: memory is unified across
  conversations; conversation origin is provenance in `metadata`).
- `scope_id` - stable id inside the scope; `scope=global` pins `"global"`.

`brain_id` comes from server config; `agent_id` is detected per session from
the MCP handshake (`clientInfo`) — no per-host config. Tool inputs never
carry identity. Every storage query carries the `brain_id` filter.

## Runtime State

Implemented and tested (197 tests): the full tool surface
(`brain_remember/search/recent/get/reinforce/forget/health/audit`), Redis
storage + index startup checks, soft delete/restore/hard delete, audit trail,
model install CLI (`model plan/pull/status`), admin CLI
(`admin restore/hard-delete`).

Stubs / not implemented: `server/resources.py`, `server/schemas.py`,
`storage/migrations.py`, `memory/normalization.py`, `memory/repository.py`,
external embedding/memory-model providers, `packages/npm-launcher`, service
Dockerfile, `brain_ingest`.

## Runtime Shape

```text
another-brain-server (python src/main.py serve)
  -> MCP transport: stdio and/or Streamable HTTP
  -> service layer: validation, identity binding, memory policy
  -> embedding provider: local SentenceTransformers (Harrier)
  -> repository: Redis 8.8 (FT.HYBRID search, TTL retention)
  -> persistent volume: Redis data + local model cache
```

Commands and the integration Redis contract: `.agents/TESTING_GUIDE.md`.
