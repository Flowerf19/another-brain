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
