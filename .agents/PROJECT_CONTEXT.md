# Project Context

## Product Boundary

Another Brain is a standalone memory service for many agent systems. It exposes
memory over MCP and owns storage, retrieval, identity boundaries, language
normalization, and memory policy.

Clients should only send observations or explicit memories and ask for recall.
The service must not depend on any client agent's loop, persona, Discord
integration, or project-specific runtime.

## Canonical Architecture

The root `README.md` is the public overview. The architecture source of truth is
`.agents/plans/another-brain-architecture.md`.

Core decisions from the architecture plan:

- MCP is the primary integration surface.
- Docker is the primary deployment shape.
- npm is a convenience launcher/adapter, not the core memory engine.
- Redis Stack is the intended MVP storage and search backend. It should store
  timeline HASH records, packed FLOAT32 vector embeddings, RediSearch indexes,
  and per-memory TTL.
- Memory is stored as timeline entries, not generic key-value notes.
- Canonical memory text is multilingual by default. The service preserves the
  natural language of each memory, records `canonical_language`, and only
  translates when deployment policy requires it.
- A lightweight memory model handles translation, normalization, and compact
  topic summaries. It is separate from chat/persona models.
- The preferred default embedding model is `microsoft/harrier-oss-v1-270m`
  because it is MIT licensed, multilingual, 640-dimensional, and strong on
  Multilingual MTEB v2. `Qwen/Qwen3-Embedding-0.6B` is the larger quality
  fallback. `jinaai/jina-embeddings-v5-text-nano` is a benchmark candidate only
  unless CC-BY-NC-4.0 is acceptable for the deployment.

## Identity Model

The main identity fields are:

- `brain_id` - shared memory namespace and isolation boundary.
- `agent_id` - calling client/agent, used for provenance and permissions.
- `subject_id` - person, project, or entity the memory is about.
- `scope` - memory boundary such as `user`, `channel`, `project`, `global`, or
  `entity`.
- `scope_id` - stable id inside the scope.
- `source` - origin detail such as `discord`, `claude`, `manual`, or `api`.

Do not partition shared memory by `agent_id` by default. Agents that share a
`brain_id` should be able to share memory unless policy denies it.

## Timeline Memory

The storage model should preserve the useful shape of March7's current T2
diary/timeline memory:

- One semantic topic over a time window becomes one timeline chunk.
- Chunks are not raw token-size slices.
- Timeline chunks carry source period bounds, source event ids, topic/kind,
  importance, embeddings, and timestamps.
- Redis HASH records store both the timeline fields and vector bytes.
- Retention uses Redis TTL on each memory key, derived from importance.
- Same-day similar chunks may merge if the merge policy permits it.
- Search is hybrid and Redis-native: vector KNN and keyword/BM25 both run
  through RediSearch on Redis, with filters, time ranges, and widening when a
  narrow time window returns nothing.

Reference implementation for the chunking model:

`https://github.com/Flowerf19/March7/tree/main/twin/shared/memory/diary`

Use it as behavior reference only. Do not couple this repo to March7 modules.

## Expected Runtime Shape

The intended MVP runtime is:

```text
another-brain-server
  -> MCP transport: stdio and/or Streamable HTTP
  -> service layer: validation, auth context, memory policy
  -> memory model: lightweight translation/normalization/summarization
  -> embedding provider: OpenAI-compatible, Ollama, Gemini, or local model
  -> repository: Redis Stack
  -> persistent volume: Redis data
```

No implementation exists yet. Placeholder module paths exist under `src/`, but
they contain no runtime logic. Exact commands are not defined.
