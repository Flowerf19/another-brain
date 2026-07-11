---
status: approved
approved: 2026-07-10
updated: 2026-07-11
owner: architecture
created: 2026-07-09
scope: step-02
depends_on: .agents/plans/01-architecture-foundation.md
---

# Step 02 - Directory And Class Architecture

This step standardizes folder names, module names, and the first class/interface
names before implementation starts. A non-runnable placeholder scaffold now
exists for review; implementation logic still belongs in a later step.

## Naming Decisions

- Repository name: `another-brain`.
- Runtime source root: `src/`.
- Service command name: `another-brain-server`.
- npm launcher package name: `@another-brain/mcp`.
- Redis key prefix: `ab`.
- MCP tool prefix: `brain_`.

Use these conventions:

- folders and modules use `snake_case`;
- module file names use **at most two words** (`redis_index.py` yes,
  `redis_memory_repository.py` no) — the package path carries the rest of
  the context;
- classes use `PascalCase`;
- request/response DTOs end with `Request`, `Result`, or `Response`;
- replaceable interfaces use `Protocol` suffix;
- concrete Redis classes use `Redis` prefix;
- MCP-facing classes use `Brain` prefix only when the name would otherwise be
  too generic.

## Proposed Runtime Tree

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
      __init__.py
      context.py
      permissions.py
      tokens.py
    mcp/
      __init__.py
      stdio.py
      http.py
      tools.py
      resources.py
      schemas.py
    memory/
      __init__.py
      models.py
      service.py
      repository.py
      normalization.py
      embeddings.py
      search.py
      retention.py
    storage/
      __init__.py
      redis_keys.py
      redis_index.py
      redis_repository.py
      migrations.py
    models/
      __init__.py
      policy.py
      registry.py
      cache.py
      installer.py
      status.py
      runtime.py
    audit/
      __init__.py
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
    unit/
    integration/
```

## Package Responsibilities

`src/app.py`

- Composition root.
- Wires config, auth, MCP transport, memory service, providers, storage, and
  audit.
- Should not contain business logic.

`src/config.py`

- Reads and validates environment/config values.
- Owns provider names, Redis connection settings, identity defaults, and feature
  flags.

`src/auth/`

- Derives trusted `brain_id` and `agent_id`.
- Checks operation permissions.
- Keeps token parsing away from memory business logic.

`src/mcp/`

- Owns MCP tool/resource schemas and transport adapters.
- Converts MCP inputs into application requests.
- Does not query Redis directly.

`src/memory/`

- Owns domain models and memory use cases.
- Contains normalization, embedding, retention, and search orchestration.
- Talks to storage only through repository protocols.

`src/storage/`

- Owns Redis key format, RediSearch index management, migrations, and the Redis
  implementation of repository protocols.
- Does not know MCP transport details.

`src/models/`

- Owns local model install policy, registry lookup, cache metadata, and explicit
  model download workflow.
- Does not perform embedding or memory-model inference directly.

`src/audit/`

- Owns audit event models and write path.
- Must avoid storing secrets.

## Required MVP Classes

### Config

`AppConfig`

- Validated runtime config.
- Includes active `brain_id`, default `agent_id`, Redis settings, provider
  settings, and embedding dimension.

`RedisConfig`

- Redis connection and index settings.

`MemoryModelConfig`

- Lightweight memory model provider, model name, API URL, and policy knobs.

`EmbeddingConfig`

- Embedding provider, model name, dimension, API URL, and API key reference.

### Auth

`AuthContext`

- Trusted identity and permission context for one request.
- Includes `brain_id`, `agent_id`, allowed operations, and optional scope
  restrictions.

`Permission`

- Enum for `read`, `write`, `delete`, and `admin`.

`TokenAuthenticator`

- Converts local config or HTTP token claims into `AuthContext`.

`PermissionChecker`

- Centralizes authorization checks before service calls.

### MCP

`BrainTools`

- Registers `brain_remember`, `brain_search`, `brain_recent`, `brain_get`,
  `brain_reinforce`, `brain_forget`, and `brain_health`.
- Delegates to `MemoryService`; does not implement memory policy itself.

`BrainResources`

- Registers `brain://schema`, `brain://health`, and
  `brain://memory/{memory_id}`.

`RememberRequest`

- Application request for `brain_remember`.

`SearchRequest`

- Application request for `brain_search` and `brain_recent`.

`ForgetRequest`

- Application request for `brain_forget`.

`ReinforceRequest`

- Application request for `brain_reinforce` — the explicit TTL re-arm after
  the LLM has fetched and validated a memory.

`HealthResult`

- Secret-free status response for `brain_health`.

### Memory Domain

`MemoryRecord`

- Canonical timeline memory record (diary model).
- Contains identity, scope, content (`topic`, `catalog`, `summary`, optional
  `content`), timeline fields, importance, timestamps, and soft-delete state.

`MemoryIdentity`

- Groups `memory_id`, `brain_id`, `agent_id`, `scope`, and `scope_id`.

`MemoryCatalog`

- Open vocabulary of catalog values (validated lowercase-kebab strings), not
  a closed enum. Starter set: `bug`, `decision`, `preference`, `task`,
  `fact`, `note`.

`MemoryScope`

- Enum for `user`, `project`, and `global`. No `channel` scope — memory is
  unified across conversations (Step 04, decision 14).

`EmbeddingVector`

- Validated embedding bytes/float representation with expected dimension.
  Model name and dimension are index-level metadata (`ab:idx:meta`), not
  record fields.

`SearchFilters`

- Scope, topic, catalog, timeline day, min importance, and time range
  filters.

`MemorySearchResult`

- Returned memory plus relevance score, score source, and widened-window marker.

### Memory Services

`MemoryService`

- Main application service.
- Implements remember, search, recent, get, reinforce, forget, and health
  use cases.

`MemoryNormalizerProtocol`

- Interface for content normalization and topic/summary generation.

`EmbeddingProviderProtocol`

- Interface for embedding canonical memory content and search queries.

`MemoryRepositoryProtocol`

- Interface for storing, retrieving, searching, soft deleting/restoring,
  reinforcing (TTL re-arm), and listing recent memories.

`MemorySearchEngine`

- Orchestrates semantic search, BM25 search, rank fusion, filters, and time
  widening.

`RetentionPolicy`

- Converts importance into the Redis TTL. Display expiry is derived from
  `EXPIRETIME` at read time; there is no stored expiry field.

### Storage

`RedisKeyBuilder`

- Builds keys such as `ab:memory:{brain_id}:{memory_id}` and audit keys
  (`ab:audit:{brain_id}:{YYYY-MM-DD}`). The type segment comes before
  `brain_id` so each key family has a fixed literal index prefix.

`RedisIndexManager`

- Creates, verifies, and migrates the RediSearch index.

`RedisMemoryRepository`

- Concrete Redis Stack implementation of `MemoryRepositoryProtocol`.
- Lives in `storage/redis_repository.py` together with `RedisMemoryMapper`.

`RedisMemoryMapper`

- Converts between `MemoryRecord` and Redis HASH fields, including packed
  FLOAT32 embedding bytes.

`MigrationRunner`

- Runs explicit storage/index migrations and prevents silent schema drift.

### Model Install

`ModelInstallPolicy`

- Parsed policy for `disabled`, `manual`, `lazy`, and `on_start` model download
  behavior.

`ModelRegistry`

- Resolves configured model names to provider-specific download metadata.

`ModelCache`

- Locates cached local models and records model metadata.

`ModelInstaller`

- Performs explicit or policy-approved model downloads.

`ModelStatus`

- Secret-free status object used by health and CLI output.

`ModelChecksumVerifier`

- Verifies downloaded files when checksum or digest data is available.

`ModelRuntimeProfile`

- Captures selected model weight precision, embedding output precision, Redis
  vector dtype, device, and embedding normalization behavior.

### Audit

`AuditEvent`

- Records memory writes, reads when needed, deletes, migrations, and admin
  operations.

`AuditService`

- Writes secret-free audit events through storage.

## Not In Step 02

Do not add these yet:

- runtime logic under `src/`;
- package manifests;
- Docker files;
- Redis schema field details;
- MCP JSON schemas;
- provider-specific implementations;
- `brain_ingest`.

## Review Decisions

Approve or change these before moving to Step 03:

1. Keep runtime source directly under `src/`.
2. Do not add a nested `src/another_brain/` directory.
3. Keep MCP adapters under `src/mcp/`.
4. Keep domain/use-case logic under `src/memory/`.
5. Keep Redis implementation under `src/storage/`.
6. Use `Protocol` suffix for replaceable interfaces.
7. Use `MemoryRecord` as the central domain object name.
8. Use `MemoryService` as the main application service name.
9. Use `RedisMemoryRepository` as the concrete Redis storage adapter.
10. Keep `brain_ingest` outside the first implementation class set.
11. Keep model download/cache code under `src/models/`, not under
    `src/memory/embeddings.py`.
12. Do not auto-download models during package install.

## Next Slice After Approval

Step 03 should define model install and download policy. After that, Step 04
should define the memory record and Redis index contract:

- memory fields;
- Redis key format;
- RediSearch schema;
- TTL policy;
- migration/reindex rules.
