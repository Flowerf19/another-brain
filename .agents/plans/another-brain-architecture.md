---
status: draft
owner: architecture
created: 2026-07-09
---

# Another Brain Architecture

`Another Brain` is a standalone memory service for agent systems. It should be
usable by Claude, Codex, Discord bots, local chat bots, or any other MCP-capable
host without knowing how those agents are implemented.

The service owns memory storage, retrieval, identity boundaries, and policy. The
client agent only sends observations or explicit memories and asks for recall.

## Goals

- Provide one shared long-term memory store for many agents.
- Support MCP as the primary integration surface.
- Track which agent wrote or read a memory through `agent_id`.
- Keep agent implementation details outside the service contract.
- Store canonical memory text in the memory's natural language by default, with
  explicit language metadata and optional translation policy.
- Add a lightweight memory model for translation, normalization, and compact
  topic summaries; keep it separate from any chat/persona model.
- Run locally first, with a Docker deployment that includes persistent storage.
- Allow an npm package to act as a convenient MCP launcher, not as the core
  storage implementation.

## Non-Goals

- Do not depend on March7, Evernight, Discord, or any project-specific runtime.
- Do not require a chat framework, persona system, or agent loop.
- Do not require a heavyweight chat LLM. Any model inside Another Brain should
  be a small memory model with narrow prompts and replaceable providers.
- Do not partition shared memory by `agent_id` by default; that would prevent
  agents from sharing the same brain.

## Product Shape

Another Brain has three install shapes:

1. **Docker service**
   - Primary deployment.
   - Runs the MCP server and connects to Redis Stack.
   - Best for shared memory across multiple agents and long-lived data.

2. **MCP stdio adapter**
   - Local process launched by an MCP host.
   - Can either run the service in-process for simple local use or proxy to a
     Docker/HTTP service.

3. **npm launcher**
   - Convenience package such as `@another-brain/mcp`.
   - Provides a familiar MCP config for clients that prefer `npx`.
   - Should not reimplement the memory engine in Node unless the project later
     chooses a TypeScript core.

## Identity Model

Identity is the core contract. The server should infer trusted identity from
configuration or auth whenever possible, instead of requiring the LLM to provide
it correctly in every tool call.

| Field | Meaning | Source |
| --- | --- | --- |
| `brain_id` | Shared memory namespace, e.g. `flowerf-main` | server config, token claim, or admin input |
| `agent_id` | Calling agent/client, e.g. `claude-desktop`, `march7`, `codex` | env var, token claim, MCP adapter config |
| `subject_id` | Person/project/entity the memory is about | tool input |
| `scope` | Memory boundary: `user`, `channel`, `project`, `global`, `entity` | tool input |
| `scope_id` | Stable id inside the scope | tool input |
| `source` | Origin detail such as `discord`, `claude`, `manual`, `api` | tool input or adapter default |

`brain_id` is the isolation boundary. `agent_id` is provenance and permission
context. Agents that share a `brain_id` can share memories unless policy says
otherwise.

## Core Data Model

The canonical storage model remains a **timeline**. Another Brain is not a
generic key-value note store; it stores dated memory entries that can be recalled
by semantic meaning, keyword match, subject, scope, and time.

Each timeline memory record should be explicit, versioned, and filterable.

```text
memory_id: uuid
brain_id: string
agent_id: string
scope: user | channel | project | global | entity
scope_id: string
subject_id: string | null
kind: fact | preference | event | summary | profile | note
topic: string
topic_display: string | null
content: string
original_content: string | null
original_language: string | null
canonical_language: string
summary: string | null
period_start: timestamp | null
period_end: timestamp | null
timeline_day: yyyy-mm-dd
source_event_ids: string[]
chunk_strategy: topic_timeline | explicit | imported
merge_count: integer
tags: string[]
importance: 1..5
confidence: 0.0..1.0
metadata: object
source: string | null
observed_at: timestamp
created_at: timestamp
updated_at: timestamp
expires_at: timestamp | null
deleted_at: timestamp | null
memory_model: string | null
embedding_model: string
embedding_dim: integer
embedding: bytes
schema_version: integer
```

The MVP storage backend should be Redis Stack. Redis is the source of truth for
timeline records and retrieval: each memory is stored as a Redis HASH, the
embedding is stored as packed FLOAT32 bytes in that HASH, RediSearch indexes the
HASH fields, per-memory TTL is applied to the HASH key, and both vector KNN and
BM25 search execute through Redis `FT.SEARCH`.

The RediSearch schema must include `brain_id`, `scope`, `scope_id`,
`subject_id`, `agent_id`, `topic`, `kind`, `tags`, language fields, and time
fields as indexed filters. It should index canonical multilingual
`content`/`summary` as TEXT fields and the packed `embedding` as a VECTOR HNSW
field with the configured dimension and cosine distance. For multilingual BM25,
prefer `NOSTEM` or an explicit per-language analyzer strategy; do not assume
English stemming is correct for every memory.

Timeline fields are first-class:

- `content` is the canonical memory text used for search and recall.
- `topic` is the stable topic slug for a topic-timeline chunk.
- `topic_display` is the optional human-readable topic label.
- `canonical_language` records the language of `content`, using a stable code
  such as `en`, `vi`, `ja`, or `mixed`.
- `original_content` keeps the raw source text when normalization changes the
  source wording or when preservation is useful.
- `observed_at` is when the source event happened.
- `period_start` and `period_end` bound the source conversation/event window
  covered by a timeline chunk.
- `created_at` is when Another Brain stored the memory.
- `updated_at` changes when a memory is merged or corrected.
- `timeline_day` is derived from `period_start` in the configured timezone, or
  `observed_at` for point memories.
- `source_event_ids` keeps provenance for the raw messages/events summarized by
  the chunk.
- `expires_at` supports retention by importance or policy.

This keeps the useful behavior of the current T2 timeline: memories are not just
facts; they are time-positioned summaries/events that can be widened by date
range when a narrow search returns nothing.

## Reference: Current T2 Chunking

The starting reference is March7's current T2 diary implementation:

- GitHub: `https://github.com/Flowerf19/March7/tree/main/twin/shared/memory/diary`
- Local code: `twin/shared/memory/diary/`
- Write entrypoint: `twin/shared/tools/modules/memory/consolidate_memory_tool.py`
- Read entrypoint: `twin/shared/tools/modules/memory/search_memory_tool.py`

Agents implementing this plan should inspect that directory before changing the
chunking policy. The current architecture is:

1. T1 messages are collected for one scope, either from local active memory or
   from shipped entries.
2. A summarizer reads that message window and returns up to five topic objects.
3. Each topic object becomes one T2 timeline chunk. This is the important
   boundary: chunks are semantic topic summaries over a time window, not raw
   token-size slices.
4. Each chunk is embedded with the passage embedding prefix, then stored as a
   Redis HASH under the timeline index.
5. The stored chunk carries `topic`, `topic_display`, `importance`,
   `period_start`, `period_end`, `source_entry_ids`, `day`, and `embedding`.
6. `day` is derived from the source period, not from the time the summarizer ran.
   This makes a late consolidation still land on the day the conversation
   happened.
7. The write path attempts a same-scope same-day merge before appending a new
   chunk. It finds the nearest existing chunk by vector search, requires cosine
   similarity above the configured merge floor, refuses merges that would exceed
   the configured max characters, concatenates old and new summaries, re-embeds,
   unions source ids, expands the period bounds, keeps max importance, and
   refreshes TTL from importance.
8. Search is timeline-aware and Redis-native: semantic KNN and BM25 both run as
   Redis `FT.SEARCH` queries, then results are fused, filtered by scope and
   optional time range, and widened by policy when a narrow time window returns
   no result.

Current M7 diary field inventory:

| Category | Count | Fields |
| --- | ---: | --- |
| Stored Redis HASH fields | 12 | `user_id`, `summary`, `topic`, `topic_display`, `importance`, `created_at`, `version`, `day`, `period_start`, `period_end`, `source_entry_ids`, `embedding` |
| RediSearch indexed fields | 11 | `user_id`, `topic`, `topic_display`, `summary`, `importance`, `created_at`, `version`, `day`, `period_start`, `period_end`, `embedding` |
| Parsed/returned fields | 4 | `summary_id` from the Redis key, `content` alias from `summary`, KNN `score`, BM25 `_score` |
| Redis key policy | not a field | key format `timeline:summary:{summary_id}`; TTL is applied with Redis `EXPIRE` from `importance` |

M7 stores the vector directly in Redis, not in a separate vector database:
`embedding` is packed FLOAT32 bytes on the same Redis HASH as the text and
metadata. RediSearch indexes that HASH with `embedding VECTOR HNSW ... COSINE`
and `summary TEXT`, so KNN vector search and BM25 lexical search both run on
Redis through `FT.SEARCH`.

M7 TTL policy:

| Importance | TTL |
| ---: | --- |
| 5 | 365 days |
| 4 | 180 days |
| 3 | 90 days |
| 2 | 30 days |
| 1 | 7 days |

Another Brain should keep the same Redis-native shape: one HASH per memory,
packed vector bytes inside the HASH, RediSearch indexing text/tag/numeric/vector
fields together, and retention controlled by Redis TTL.

Another Brain should preserve this shape, but rename the fields and boundaries
for a standalone MCP service:

- current `summary_id` -> `memory_id`
- current `user_id` filter -> explicit `brain_id` + `scope` + `scope_id` +
  optional `subject_id`
- current `summary` -> canonical multilingual `content` and optional display
  `summary`
- current `topic` -> `topic`
- current `topic_display` -> `topic_display`
- current `source_entry_ids` -> `source_event_ids`
- current `day` -> `timeline_day`
- current `embedding` -> packed FLOAT32 `embedding` stored in Redis HASH
- current topic chunk -> `chunk_strategy=topic_timeline`

Fields Another Brain should add on top of the M7 diary shape:

- `brain_id` for namespace isolation;
- `agent_id` for provenance and authorization context;
- explicit `scope` and `scope_id` instead of overloading `user_id`;
- `subject_id` for the person/project/entity the memory is about;
- `kind` for fact/preference/event/summary/profile/note classification;
- `original_content`, `original_language`, and `canonical_language`;
- `tags` for secondary labels beyond the primary topic;
- `confidence`;
- `metadata` for provider/client details;
- `source` for origin such as `discord`, `claude`, `manual`, or `api`;
- `observed_at`, `updated_at`, `expires_at`, and `deleted_at`;
- `chunk_strategy` and `merge_count`;
- `memory_model`, `embedding_model`, `embedding_dim`, and `schema_version`.

Do not preserve project-specific shortcuts such as storing channel memory under
`user_id=channel_id`. The new schema should represent scope explicitly.

## Language and Memory Model Policy

Another Brain should be multilingual by default. The previous English-only
canonical plan is replaced by a language-preserving policy because the preferred
embedding direction is now a strong multilingual model.

The service should own normalization. Clients may send raw text in any language,
but the stored timeline chunk should be normalized server-side:

1. Detect or accept `original_language`.
2. Normalize and compress the memory in its natural language unless policy
   explicitly requests translation.
3. Preserve names, ids, commands, paths, dates, numbers, and quoted user
   preferences exactly.
4. Store the normalized result in `content`.
5. Store the language of `content` in `canonical_language`.
6. Optionally keep the raw source text in `original_content` for audit/debug.
7. Embed canonical `content`, not an optional translated/debug copy.

Translation remains a deployment policy, not the default:

- `preserve` keeps the memory in its natural language.
- `translate_to_en` stores English canonical content for deployments that use
  weak English-only models.
- `dual` can store source-language canonical `content` plus an optional English
  helper field later, but this is not required for MVP.

Use a dedicated lightweight memory model for this step. It is not a chat model
and should not know agent persona. Its jobs are narrow:

- detect language and normalize memory text;
- optionally translate when policy requires it;
- produce compact timeline chunks from observations;
- normalize relative dates into absolute dates when the caller supplies time
  context;
- return strict JSON for storage.

Suggested config:

```text
MEMORY_MODEL_PROVIDER=openai_compat | ollama | gemini | local
MEMORY_MODEL_NAME=...
MEMORY_MODEL_API_URL=...
MEMORY_MODEL_API_KEY=...
MEMORY_CANONICAL_LANGUAGE=auto
MEMORY_TRANSLATION_POLICY=preserve
STORE_ORIGINAL_CONTENT=true
```

The memory model is separate from the embedding provider. Changing the memory
model should not require reindexing unless the canonical text is regenerated.

## Memory Write Policy

Another Brain should support two write paths:

1. **Explicit memory write**
   - Tool: `brain_remember`.
   - Client sends the final memory text.
   - Server validates, normalizes according to language policy, embeds,
     deduplicates, stores, and returns `memory_id`.
   - This is the required MVP path.

2. **Observation ingest**
   - Tool: `brain_ingest`.
   - Client sends raw messages/events with timestamps and actors.
   - Server uses the lightweight memory model to produce multilingual topic
     timeline chunks.
   - This richer ingest path is not required for MVP; MVP can start with
     explicit memory writes.

The server should never silently trim or delete source data unless the caller
explicitly requests that behavior. Data loss policy belongs in the memory
service, not in each agent.

## Retrieval Policy

Search should be hybrid by default:

- vector similarity for semantic recall;
- keyword/BM25 search for names, ids, and exact terms;
- reciprocal-rank or similar fusion;
- filters for `brain_id`, `scope`, `scope_id`, `subject_id`, `kind`, `agent_id`,
  `tags`, and time range;
- similarity floor to avoid injecting weak memories;
- timeline widening: search within the requested date window first, then widen
  by policy if no results are found, and label the wider result window.

The tool output should expose enough evidence for the agent to judge relevance:

```text
memory_id
content or summary (canonical multilingual)
original_language
canonical_language
kind
subject_id
scope/scope_id
source
agent_id
observed_at
importance
relevance score
```

## MCP Surface

MCP tools should be small and stable. Tool names use `brain_*` to stay short.

### `brain_remember`

Stores one explicit memory.

Required inputs:

- `content`
- `scope`
- `scope_id`

Optional inputs:

- `subject_id`
- `kind`
- `tags`
- `importance`
- `confidence`
- `observed_at`
- `metadata`

Server-filled fields:

- `brain_id`
- `agent_id`
- `memory_id`
- normalized `content`
- `canonical_language`
- `original_language`
- embeddings
- timestamps

### `brain_search`

Searches memories.

Inputs:

- `query`
- `scope`
- `scope_id`
- `subject_id`
- `k`
- `since`
- `until`
- `kind`
- `tags`
- `include_agent_ids`

`query` may be empty when the caller wants recent memories.

### `brain_recent`

Returns recent memories by scope/time without requiring embeddings.

### `brain_get`

Fetches one memory by `memory_id`.

### `brain_forget`

Soft-deletes one memory. Hard delete should be an admin-only operation.

### `brain_health`

Reports service status, storage status, index version, embedding model, and
embedding dimension. It must not return secrets.

## MCP Resources

Resources should expose machine-readable context, not agent instructions:

- `brain://schema` - current memory schema and version.
- `brain://health` - same health data as `brain_health`.
- `brain://memory/{memory_id}` - one memory record if authorized.

Prompts are optional. If added, they should be generic usage hints such as
`brain-recall-guidance`, not agent-specific behavior.

## Auth and Permissions

Local stdio can rely on local process trust, but the server should still require
configuration:

```text
ANOTHER_BRAIN_ID=flowerf-main
ANOTHER_BRAIN_AGENT_ID=claude-desktop
ANOTHER_BRAIN_API_TOKEN=...
```

Remote HTTP must authenticate every request. The token should map to:

- allowed `brain_id` values;
- caller `agent_id`;
- allowed operations: read, write, delete, admin;
- optional scope restrictions.

The LLM should not be trusted to declare its own `agent_id` or permissions.
Tool inputs may include `agent_id` only for admin/debug use.

## Storage Architecture

Recommended MVP:

```text
another-brain-server
  -> MCP transport: stdio and/or Streamable HTTP
  -> service layer: validation, auth context, memory policy
  -> memory model: lightweight translation/normalization/summarization
  -> embedding provider: OpenAI-compatible, Ollama, Gemini, or local model
  -> repository: Redis Stack
  -> persistent volume: Redis data
```

Redis keys should be prefixed:

```text
ab:{brain_id}:memory:{memory_id}
ab:{brain_id}:audit:{date}
```

Redis Stack is the memory database, vector store, lexical search store, and
retention system. Do not introduce a separate vector database for MVP.

Each memory HASH should include canonical multilingual text, language fields,
topic fields, metadata, period fields, source ids, importance, timestamps, and
packed FLOAT32 embedding bytes. The HASH key TTL should be derived from
importance and refreshed when a merge updates the memory.

RediSearch should index those HASH documents. A first implementation can use one
global index with `brain_id` as a required filter, or one index per `brain_id`.
Start with one global index for simpler migrations; never run a query without
the `brain_id` filter. The index must support:

- TEXT fields for canonical multilingual `content`/`summary`, searched with
  BM25;
- TAG fields for `brain_id`, `scope`, `scope_id`, `subject_id`, `agent_id`,
  `topic`, `kind`, tags, and `timeline_day`;
- NUMERIC/SORTABLE fields for `created_at`, `observed_at`, `period_start`,
  `period_end`, `importance`, and schema/version fields;
- VECTOR HNSW field for `embedding`, using FLOAT32 and cosine distance.

KNN search, BM25 search, recent/timeline reads, and same-day merge candidate
lookup should all be Redis `FT.SEARCH` queries against this index. This keeps
storage, TTL, lexical recall, semantic recall, and merge behavior consistent.

## Embedding Policy

The service owns embedding configuration.

Required config:

```text
EMBEDDING_PROVIDER=openai_compat | ollama | gemini | local
EMBEDDING_MODEL=...
EMBEDDING_DIM=...
EMBEDDING_API_URL=...
EMBEDDING_API_KEY=...
```

Local model acquisition should follow the dedicated model install policy in
`.agents/plans/03-model-install-policy.md`. Do not download large models during
package installation. Local models should be pulled explicitly or through a
configured startup/lazy policy, with cache metadata and embedding dimension
checks.

Embedding precision policy is also defined in that plan. MVP should treat Q8/Q4
as local model weight quantization only. Redis vector storage should remain
`FLOAT32` until lower-precision vector storage has recall, migration, and index
compatibility tests.

Recommended embedding candidates as of 2026-07:

| Role | Model | License | Params | Dim | Max tokens | MTEB Multilingual v2 | Operational notes |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| Default | `microsoft/harrier-oss-v1-270m` | MIT | 268M | 640 | 32,768 | 66.55, rank 17 | Best current fit: open license, multilingual, moderate Redis vector cost, SentenceTransformers compatible. Use normalized 640-d output and Redis `FLOAT32`. |
| Quality fallback | `Qwen/Qwen3-Embedding-0.6B` | Apache-2.0 | 596M | 1,024 | 32,768 | 64.34, rank 18 | Strong model, but larger than the preferred budget and doubles Redis vector bytes versus 512-dim class models. Use when memory/VRAM budget allows or via external provider. |
| Benchmark only | `jinaai/jina-embeddings-v5-text-nano` | CC-BY-NC-4.0 | 212-239M | 768 | 8,192 | 65.52, rank 19 | Good score and small runtime footprint, but non-commercial license, `trust_remote_code`, and `peft` dependency make it unsuitable as the default for a reusable MCP tool. |

Redis vector bytes before index overhead:

```text
Harrier 640 dim      -> 2,560 bytes per memory
Qwen3 0.6B 1024 dim  -> 4,096 bytes per memory
Jina v5 nano 768 dim -> 3,072 bytes per memory
```

Evidence links:

- Harrier model card: `https://huggingface.co/microsoft/harrier-oss-v1-270m`
- Qwen3 Embedding 0.6B model card:
  `https://huggingface.co/Qwen/Qwen3-Embedding-0.6B`
- Jina v5 text nano model card and license:
  `https://huggingface.co/jinaai/jina-embeddings-v5-text-nano`
- MTEB Leaderboard: `https://huggingface.co/spaces/mteb/leaderboard`

Rules:

- store the packed FLOAT32 embedding bytes in the Redis HASH memory record;
- store `embedding_model` and `embedding_dim` on every memory;
- refuse writes when vector dimension mismatches the active index;
- expose the active embedding model through `brain_health`;
- embed canonical `content`, not `original_content` or optional translation
  helper fields;
- require a migration/reindex command when changing dimensions.
- for Harrier, prefer the SentenceTransformers path because it already defines
  pooling and normalization; raw Transformers usage must reproduce last-token
  pooling and explicit normalization.
- treat Q8/Q4 as local model-weight quantization experiments, not as the Redis
  vector storage format.

## Packaging

### Docker

Primary install target:

```text
docker compose up -d
```

Compose should include:

- `another-brain` service;
- `redis-stack` service;
- named volume for Redis data;
- `.env` for embedding provider and auth;
- healthcheck for MCP/HTTP and Redis.

### npm

Secondary install target:

```text
npx -y @another-brain/mcp
```

The npm package should:

- read MCP client env/config;
- start a stdio adapter;
- connect to a configured HTTP service, or optionally start Docker if enabled;
- avoid owning the database itself.

This keeps the easy MCP UX without making Node the source of truth.

## Suggested Repository Layout

```text
another-brain/
  README.md
  docs/
    architecture.md
    mcp-tools.md
    deployment.md
  src/
    main.py
    app.py
    config.py
    errors.py
    auth/
      context.py
      permissions.py
      tokens.py
    mcp/
      stdio.py
      http.py
      tools.py
      resources.py
      schemas.py
    memory/
      models.py
      service.py
      repository.py
      normalization.py
      search.py
      embeddings.py
      retention.py
    storage/
      redis_keys.py
      redis_index.py
      redis_repository.py
      migrations.py
    models/
      policy.py
      registry.py
      cache.py
      installer.py
      status.py
      runtime.py
    audit/
      models.py
      service.py
  docker/
    Dockerfile
    docker-compose.yml
  packages/
    npm-launcher/
      package.json
      src/
  tests/
```

## MVP Milestones

1. Core memory model and Redis Stack repository.
2. Lightweight memory model abstraction for multilingual normalization.
3. Model install/cache policy for local embedding and memory models.
4. Embedding provider abstraction and `brain_health`.
5. MCP stdio server with `brain_remember`, `brain_search`, `brain_recent`.
6. Docker Compose deployment.
7. Auth context with server-filled `brain_id` and `agent_id`.
8. `brain_get` and `brain_forget` with audit log.
9. npm launcher that proxies to the service.
10. Optional richer observation ingest pipeline.

## Migration From Existing T2

Existing T2 data can be migrated later by mapping:

```text
old user_id      -> subject_id or scope_id
old summary      -> content
old original text -> original_content when available
old topic        -> topic; optionally also tags/kind
old topic_display -> topic_display
old importance   -> importance
old period_start -> period_start/observed_at/timeline_day
old period_end   -> period_end
old source_entry_ids -> source_event_ids
old created_at   -> created_at
old embedding    -> re-embed canonical content unless source text,
                    embedding model, and embedding dimension are unchanged
source           -> "march7-t2-migration"
agent_id         -> migration agent id
brain_id         -> configured destination brain
```

Do not preserve the old `user_id=channel_id` shortcut in the new schema. The
new schema should represent channel and user scopes explicitly.

## Open Decisions

- Whether Redis Stack remains the only supported backend after MVP.
- Whether server-side summarization belongs in core or in a plugin.
- Whether remote MCP should be exposed directly or through a small gateway.
- Whether memory export/import should use JSONL, SQLite, or both.
- Whether `brain_id` should support multiple users on one server from day one.
