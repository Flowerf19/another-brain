---
status: approved
approved: 2026-07-10
updated: 2026-07-11
owner: architecture
created: 2026-07-09
scope: step-01
---

# Step 01 - Architecture Foundation

This is the first small review slice for Another Brain. It defines the system
shape only. It does not choose a framework, create runtime code, add Docker, or
define exact test commands.

## Current Repo State

The repository is documentation-only right now:

- `README.md`
- `.agents/README.md`
- `.agents/PROJECT_CONTEXT.md`
- `.agents/AGENT_RULES.md`
- `.agents/TESTING_GUIDE.md`
- `.agents/plans/another-brain-architecture.md`

There is now a non-runnable placeholder scaffold under `src/`, `docs/`,
`docker/`, `packages/`, and `tests/`. There is no package manifest, Docker file,
Compose file, env example, implementation logic, or executable test suite yet.
Treat all runtime pieces below as intended architecture.

## One Sentence

Another Brain is a standalone MCP-first timeline memory service that lets many
agents write and recall shared long-term memory through a stable `brain_*`
tool surface.

## System Boundary

Another Brain owns:

- memory tool contracts;
- trusted identity context: `brain_id`, `agent_id`;
- validation and memory policy;
- content normalization (topic + summary, multilingual-safe);
- embedding generation;
- timeline storage and retrieval;
- retention and soft delete policy;
- health/status output.

Another Brain does not own:

- any client agent loop;
- chat persona behavior;
- Discord or project-specific integrations;
- a frontend UI;
- a separate vector database for MVP.

## Component View

```mermaid
flowchart TD
    subgraph Clients["Client side"]
        Host["MCP host / agent"]
        Npm["npm launcher<br/>optional convenience adapter"]
    end

    subgraph Transport["Transport boundary"]
        Stdio["MCP stdio"]
        Http["Streamable HTTP<br/>optional remote mode"]
    end

    subgraph Core["Another Brain service"]
        Mcp["MCP tool/resource layer"]
        Auth["Auth + identity context<br/>brain_id / agent_id"]
        Policy["Validation + memory policy"]
        Memory["Memory service"]
        Model["Lightweight memory model<br/>normalize / summarize"]
        Embed["Embedding provider"]
        Retrieve["Hybrid retrieval<br/>KNN + BM25 + filters"]
    end

    subgraph Redis["Redis Stack"]
        Hash["Memory HASH<br/>timeline fields + FLOAT32 embedding + TTL"]
        Index["RediSearch index<br/>TEXT + TAG + NUMERIC + VECTOR"]
        Audit["Audit records"]
        Volume["Persistent volume"]
    end

    Host --> Stdio
    Host --> Http
    Npm --> Stdio
    Npm --> Http

    Stdio --> Mcp
    Http --> Mcp
    Mcp --> Auth
    Auth --> Policy
    Policy --> Memory
    Memory --> Model
    Memory --> Embed
    Memory --> Retrieve
    Memory --> Hash
    Retrieve --> Index
    Index --> Hash
    Memory --> Audit
    Hash --> Volume
    Audit --> Volume
```

## First Write Path

MVP should start with explicit memory writes. Raw observation ingest can come
later.

```mermaid
sequenceDiagram
    participant Host as MCP host
    participant MCP as MCP tool layer
    participant Auth as Auth context
    participant Svc as Memory service
    participant Model as Memory model
    participant Embed as Embedding provider
    participant Redis as Redis Stack

    Host->>MCP: brain_remember(content, scope, scope_id, ...)
    MCP->>Auth: derive trusted brain_id and agent_id
    Auth->>Svc: validated memory request
    Svc->>Model: normalize content into topic and summary
    Model-->>Svc: topic + summary (+ optional detail content)
    Svc->>Embed: embed summary
    Embed-->>Svc: FLOAT32 vector
    Svc->>Redis: store HASH + vector + TTL
    Svc-->>Host: memory_id and stored memory evidence
```

## First Read Path

Search should be hybrid from the start because exact names, ids, paths, and
commands need lexical recall while paraphrased memories need semantic recall.

```mermaid
sequenceDiagram
    participant Host as MCP host
    participant MCP as MCP tool layer
    participant Auth as Auth context
    participant Svc as Memory service
    participant Embed as Embedding provider
    participant Redis as Redis Stack

    Host->>MCP: brain_search(query, scope, scope_id, filters)
    MCP->>Auth: enforce brain_id and permissions
    Auth->>Svc: validated search request
    Svc->>Embed: embed query when semantic search is used
    Embed-->>Svc: query vector
    Svc->>Redis: FT.SEARCH vector KNN
    Svc->>Redis: FT.SEARCH BM25
    Redis-->>Svc: candidate memories
    Svc->>Svc: fuse ranks, filter, apply time widening
    Svc-->>Host: memories with relevance evidence
```

## Memory Lifecycle (Activity View)

One activity diagram for the whole memory flow: how a memory is written, how
it is recalled, how the LLM closes the loop after using it, and how the
record moves through its Redis lifecycle. There is deliberately no
`brain_update` tool — the store is append-only (Step 04, decision 11), so an
update is expressed as `brain_remember` (new version) plus `brain_forget`
(old version). TTL renewal is never a code side effect: only an explicit
`brain_reinforce` re-arms it (Step 04, decision 9).

```mermaid
flowchart TD
    Start(["Agent working"]) --> Trigger{"Worth remembering,<br/>or need recall?"}

    %% ---------------- write path ----------------
    Trigger -- "remember" --> Remember["brain_remember<br/>content, scope, scope_id,<br/>catalog?, importance?"]
    Remember --> WAuth["Auth: trusted brain_id + agent_id,<br/>write permission"]
    WAuth --> WValidate["Validate: scope enum, catalog slug,<br/>content length cap"]
    WValidate --> WNormalize["Memory model normalizes:<br/>topic slug + 1-2 sentence summary<br/>+ optional detail content"]
    WNormalize --> WEmbed["Embed summary only<br/>FLOAT32 vector"]
    WEmbed --> WStore["HSET one HASH per memory<br/>ab:memory:brain_id:memory_id<br/>append-only, never merges"]
    WStore --> WTtl["EXPIRE by importance<br/>5=365d 4=180d 3=90d 2=30d 1=7d"]
    WTtl --> WAudit["Audit: remember"] --> WDone["Return memory_id"] --> Live

    %% ---------------- read path ----------------
    Trigger -- "recall" --> RKind{"Query type?"}
    RKind -- "semantic / lexical" --> RSearch["brain_search<br/>query + filters"]
    RKind -- "timeline" --> RRecent["brain_recent<br/>time range"]
    RSearch --> RHybrid["KNN + BM25, RRF fusion,<br/>cosine gate, then limit<br/>brain_id filter + deleted excluded"]
    RRecent --> RSort["Filter + sort period_start DESC<br/>brain_id filter + deleted excluded"]
    RHybrid --> RPreview["Preview list: memory_id, topic,<br/>catalog, summary, timeline_day,<br/>importance, has_content<br/>NO TTL change"]
    RSort --> RPreview
    RPreview --> RNeed{"Summary enough<br/>to answer?"}
    RNeed -- "yes" --> Use["LLM uses the memory"]
    RNeed -- "need detail" --> RGet["brain_get memory_id<br/>pure read, NO TTL change"] --> Use

    %% ---------------- close the loop ----------------
    Use --> Judge{"LLM verdict<br/>on this memory?"}
    Judge -- "correct and valuable" --> Reinforce["brain_reinforce memory_id<br/>re-arm full importance TTL,<br/>bump updated_at + audit"] --> Live
    Judge -- "wrong / stale" --> Forget["brain_forget memory_id<br/>deleted_at = now,<br/>TTL shrunk to grace window + audit"]
    Judge -- "info changed: update" --> Update["Update = append-only pair:<br/>brain_remember new version,<br/>brain_forget old version"]
    Judge -- "no verdict" --> NoOp["Do nothing.<br/>TTL keeps counting down"] --> Live
    Update --> Remember
    Update -.-> Forget

    %% ---------------- record lifecycle ----------------
    subgraph Redis["Record lifecycle in Redis"]
        Live["LIVE<br/>visible to search/recent,<br/>TTL counting down"]
        SoftDel["FORGOTTEN soft<br/>excluded from every query<br/>at index level, recoverable<br/>during grace window"]
        Gone(["GONE<br/>Redis expired or deleted the key"])
    end

    Forget --> SoftDel
    Live -- "TTL expires,<br/>never reinforced" --> Gone
    SoftDel -- "grace window ends" --> Gone
    SoftDel -- "admin restore: HDEL deleted_at,<br/>re-arm importance TTL + audit" --> Live
    SoftDel -- "admin hard delete: DEL + audit" --> Gone
```

Reading notes:

- Every read (`brain_search`, `brain_recent`, `brain_get`) is pure — no read
  ever re-arms TTL. The only paths back to a fresh TTL are `brain_reinforce`
  and admin restore.
- The "no verdict" branch is the designed failure direction: an untouched
  memory simply expires at its importance baseline. The system fails toward
  forgetting, never toward bloat.
- `brain_health` is outside this flow — it is a status probe, not a memory
  operation.

## Minimum Public Surface

Step 01 assumes these tool names, but detailed schemas should be reviewed in a
later API step:

- `brain_remember`
- `brain_search`
- `brain_recent`
- `brain_get`
- `brain_reinforce`
- `brain_forget`
- `brain_health`

`brain_ingest` is intentionally outside the first slice.

## Storage Direction

Use Redis Stack as the MVP storage system:

- one Redis HASH per memory;
- packed FLOAT32 embedding stored on the same HASH;
- RediSearch index over text, tags, numeric time fields, and vector field;
- Redis TTL for retention;
- `brain_id` filter required for every query.

Do not introduce a separate vector database in the first implementation.

## Review Decisions

Approve or change these before moving to Step 02:

1. Another Brain is MCP-first and has no frontend in the initial architecture.
2. Docker service is the primary deployment shape; npm is only a launcher or
   adapter.
3. Redis Stack is the only MVP database, search engine, vector store, and TTL
   mechanism.
4. `brain_id` is the storage isolation boundary.
5. `agent_id` is provenance and permission context, not the default memory
   namespace.
6. First write path is `brain_remember`; raw observation ingest comes later.
7. Search is hybrid from the start: vector KNN plus BM25 through RediSearch.

## Next Slice After Approval

Step 02 should define directory names, module names, and the first class names.
After that, Step 03 should define the memory record and Redis index contract:

- memory fields;
- Redis key format;
- RediSearch schema;
- TTL policy;
- migration/reindex rules.
