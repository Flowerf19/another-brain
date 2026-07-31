---
status: in-progress
created: 2026-07-29
last_updated: 2026-07-31
---

# Plan 07 — Clean embedded rebuild (remove Docker, Redis, and Torch)

## Summary

Rebuild another-brain as a standalone, lightweight MCP tool whose final source,
wheel, normal runtime, tests, install path, and documentation contain no Docker
or Redis dependency. The target stack is:

- ordinary SQLite tables as the single source of truth;
- SQLite FTS5 for weighted lexical/BM25 retrieval;
- `sqlite-vec` scalar `vec_distance_cosine` for exact vector retrieval, with a
  NumPy exact-scan fallback when the extension is unavailable;
- app-layer reciprocal-rank fusion (RRF);
- raw ONNX Runtime CPU + Hugging Face `tokenizers`;
- pinned Harrier OSS v1 270M ordinary q4 weights;
- MCP stdio as the default transport and a packaged `another-brain` executable.

### Replacement strategy — use `main` as the external oracle, delete early

Redis and Docker do **not** need to remain in branch `v0.11.0`. The complete
legacy implementation is permanently available on `main` at baseline commit
`edc0e57` (or a later explicitly recorded maintenance commit). When comparison
is needed, run it from a separate Git worktree; never switch or copy Redis code
back into the clean branch:

```bash
git worktree add ../another-brain-main main
```

Therefore the clean branch uses this order:

1. Approve the desired contracts and record the exact `main` oracle commit.
2. Establish the final `src/another_brain/` package shell and preserve only
   backend-neutral domain/tool response contracts.
3. Delete Redis, Docker, Torch/SentenceTransformers, their config, tests,
   dependencies, and old composition from `v0.11.0` **before** implementing the
   new storage/retrieval modules. Keep the branch green with package/domain
   tests; temporary feature incompleteness is acceptable inside the
   in-progress major-version branch.
4. Build SQLite, FTS5, scalar vector retrieval, RRF, ONNX q4, service, and MCP
   vertically in the clean tree. There is no `STORAGE_BACKEND` flag and no
   Redis implementation of the new protocols.
5. Compare against `main` only through deterministic fixtures, JSONL artifacts,
   or an external worktree process.
6. Perform final migration/cutover and release gates without ever reinstalling
   Redis or Docker in `v0.11.0`.

A Redis JSONL exporter belongs to a maintenance commit/release based on `main`,
not to this branch. `v0.11.0` contains only the neutral JSONL importer.

The old hybrid ranking is **not** an oracle where it is known to be wrong. Its
universal cosine gate can discard an exact `content` BM25 match because only
`topic + summary` is embedded. The new retrieval contract fixes that behavior:
pure lexical candidates do not need to pass the vector cosine floor.

### Locked product decisions

1. **Canonical store** — regular SQLite tables, not `vec0`, LanceDB, DuckDB,
   Redis, or an ANN sidecar.
2. **Lexical retrieval** — FTS5 indexes `topic`, `summary`, and `content` with
   initial BM25 field weights `5:3:1` and `unicode61 remove_diacritics 2`.
3. **Vector retrieval** — one normalized FLOAT32[640] vector per memory,
   searched exactly with `vec_distance_cosine`; NumPy is the compatibility
   fallback.
4. **Fusion** — equal-weight two-branch RRF with `k=60`; vector candidates must
   meet cosine `>=0.30`, while lexical candidates remain eligible without a
   cosine gate. Final ordering is deterministic.
5. **Embedding runtime** — raw `onnxruntime` CPUExecutionProvider plus
   `tokenizers`; the ONNX graph already returns normalized
   `sentence_embedding [batch, 640]`.
6. **Model artifact** — q4 at immutable ONNX-community revision
   `d59c919d0159aea2c19ed7d04288fcdd048d0f9c`. Required pair and SHA-256:
   - `onnx/model_q4.onnx` —
     `228dca2603b907d673dd99cf89c309c0ca68baeed127416a5e027a48e62b0f49`
   - `onnx/model_q4.onnx_data` —
     `b5a15487360f5341659480ae4b5ad60028d5f865bd329196ec8d5708bbed3118`
7. **Document payload** — exactly one unprompted embedding from
   `topic.replace("-", " ") + "\n" + summary.strip()`. `content` is
   lexical-only; catalog, metadata, scope, time, and importance are
   filters/provenance.
8. **Topic contract** — a stable retrieval subject reusable by related diary
   entries. Count the humanized slug without special tokens; target 3–8
   Harrier tokens, hard maximum 12. Do not duplicate catalog, use transient
   workflow labels, or stuff keywords.
9. **Token budgets** — count with the pinned Harrier tokenizer only:
   - humanized topic: max 12, no special tokens;
   - final topic+summary document: max 256, including special tokens;
   - final query-prompt+query: max 128, including special tokens;
   - lexical-only content: max 1,024, no special tokens.
   Reject over-limit input with actual/allowed counts; never truncate or chunk.
10. **Durable lifecycle** — persist `expires_at`; every read and both retrieval
    branches exclude `expires_at <= now` and `deleted_at IS NOT NULL` before
    branch limits. Reinforce and restore re-arm retention transactionally.
11. **Concurrency** — independent stdio processes share one SQLite file through
    WAL, `busy_timeout`, bounded write retries, short transactions, and locked
    schema/model installation.
12. **Install contract** — `[project.scripts] another-brain =
    "another_brain.cli:main"`; `uv tool install another-brain`, then invoke the
    installed `another-brain` executable. Harness configs do not use unpinned
    `uvx`.

### Target module boundaries

```text
src/another_brain/
  cli.py                         command parser and console entry point
  app.py                         composition root and resource lifecycle
  config.py                      Redis-free runtime configuration
  domain/
    models.py                    diary, identity, filters, search result
    retention.py                 importance -> durable expiry policy
  embedding/
    manifest.py                  pinned model/artifact/input contract
    installer.py                 verified download + cross-process lock
    provider.py                  raw ONNX Runtime provider
    payload.py                   topic+summary and query construction
    budgets.py                   tokenizer-based hard limits
  storage/
    connection.py                SQLite connection policy and busy retry
    schema.py                    DDL and migration runner
    repository.py                memory CRUD and lifecycle
    audit.py                     secret-free SQLite audit persistence
  retrieval/
    query.py                     safe FTS5 query construction
    lexical.py                   FTS5 candidate source
    vector.py                    sqlite-vec/NumPy exact candidate source
    fusion.py                    deterministic RRF
    service.py                   hybrid orchestration
  mcp/
    tools.py                     stable brain_* tool surface
    server.py                    stdio and optional localhost HTTP
```

Protocols exist for service isolation and unit tests, not backend selection:

```text
MemoryRepository: store/get/recent/reinforce/soft_delete/restore/hard_delete
MemoryRetriever:  search(query text + vector + filters) -> previews
AuditRepository:  record/list_day
EmbeddingProvider: embed_document/embed_query + health state
```

### SQLite schema contract

One database file (`brain.sqlite3`) contains:

- `schema_migrations(version, checksum, applied_at)`;
- `embedding_profiles` with model, source/artifact revisions, q4 variant,
  dimension, dtype, normalization, query prompt, and input version;
- `memories` with internal integer `row_id`, public identity/scope, topic,
  catalog, summary, content, timeline fields, importance, `expires_at`,
  `deleted_at`, metadata JSON, embedding profile, FLOAT32 embedding BLOB, and
  record version;
- external-content `memory_fts(topic, summary, content)` with insert/delete/
  update triggers;
- `audit_events` containing structural mutation facts and no memory text.

`memories.embedding` is little-endian FLOAT32 and has
`CHECK(length(embedding)=2560)`. The active embedding profile is q4,
640-dimensional, normalized, and `embedding_input_version=2`. Changing the
model, precision, dimension, tokenizer, prompt, or document payload is an
explicit migration and re-embedding operation.

Connection policy for every connection:

```text
foreign_keys = ON
journal_mode = WAL
synchronous = NORMAL
busy_timeout = 5000 ms
page_size = 16384 (set before first schema creation)
```

Writes use `BEGIN IMMEDIATE`, bounded exponential backoff with jitter for
`SQLITE_BUSY`, and no model inference or network I/O inside a transaction.

### Retrieval contract

For configured `top_k`, each branch requests
`candidate_limit = min(max(4 * top_k, 40), 200)` after mandatory scope,
brain, expiry, and deletion filters.

- Lexical: safe OR query over tokenizer-compatible terms, FTS5 BM25 ascending,
  field weights topic=5, summary=3, content=1.
- Vector: exact cosine distance ascending; discard candidates below cosine
  0.30 before fusion.
- Fusion: `1 / (60 + rank)` from each branch, equal branch weights; a document
  present in both receives both contributions.
- Lexical-only candidates remain valid. This is the deliberate fix for the
  current content-match/cosine-gate bug.
- Stable tie break: fused score descending, branch count descending, best
  branch rank ascending, then `memory_id` ascending.
- A query with no safe lexical terms uses vector retrieval only.

## Success criteria

1. A clean built wheel installs with `uv tool install` on the required matrix:
   Windows x86_64, macOS 14+ ARM64, and Ubuntu 22.04/24.04 x86_64 using Python
   3.12–3.14.
2. Bare `another-brain` starts MCP stdio; a fresh profile performs remember →
   search → get → reinforce → forget without Docker, Redis, Torch, or network
   access after model installation.
3. Core dependencies are limited to MCP, ONNX Runtime, Tokenizers, NumPy,
   `platformdirs`, `sqlite-vec`, and a small cross-platform file-lock package.
4. Core source/config/tests/scripts/product docs have no Redis or Docker
   runtime path. Historical architecture plans may retain clearly marked
   superseded context.
5. Exact identifiers found only in `content` are retrievable through FTS5 even
   when topic+summary cosine is below 0.30; irrelevant vector-only hits below
   0.30 remain excluded.
6. Expired and soft-deleted rows never surface from get/recent/lexical/vector
   retrieval, including immediately after restart and under concurrent access.
7. Two independent processes can remember/search/reinforce/forget against the
   same database without corruption, lost writes, duplicate migrations, or
   unhandled `SQLITE_BUSY` in the accepted workload.
8. Redis JSONL migration preserves IDs, identity, timestamps, metadata,
   remaining TTL, soft-delete state, and audit facts; imports are resumable and
   idempotent. Embeddings are recomputed under input version 2.
9. Provisional resource gates on the reference x86_64 machine:
   - clean installed environment plus q4 model/tokenizer: <=450 MiB disk;
   - one loaded short-input embedding process: <=500 MiB steady RSS;
   - <=128-token warm embedding p95: <=100 ms;
   - 10k vector retrieval p95: <=25 ms;
   - 50k vector retrieval p95: <=75 ms;
   - 100k vector retrieval p95: <=150 ms.
10. Architecture source-of-truth, README, tool descriptions, testing guide,
    examples, and harness connectors describe only the final embedded runtime.

### Execution order

GOAL numbers and task IDs are append-only, so execution order is explicit:

```text
GOAL-008  contracts + external main oracle
GOAL-009  final package shell
GOAL-015  early destructive cleanup on v0.11.0
GOAL-001/002  quality and storage evidence (may run in parallel)
GOAL-005/010  embedding subsystem
GOAL-011  SQLite/lifecycle/audit
GOAL-012  lexical/vector/RRF retrieval
GOAL-013  service/MCP vertical slice
GOAL-014  JSONL import and cutover
GOAL-016  platform/release gate
```

## Tasks

### GOAL-001: Validate locked Harrier q4 quality and resource envelope

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Add `spikes/embedding_parity.py` using current ST+torch fp32 only as an evaluation reference and raw ONNX Runtime q4 as the target; consume graph `sentence_embedding` directly and use the pinned query prompt only for queries. | | |
| TASK-002 | Build judged Vietnamese/English query-memory pairs using version-2 topic+summary documents, no-diacritic queries, short/long inputs, exact identifiers in lexical-only content, and punctuation-only queries. | | |
| TASK-003 | Record cosine(q4, fp32), Recall@5, MRR, nDCG@10, cold/warm latency by token bucket, steady/peak RSS, and one-/two-process PSS. | | |
| TASK-004 | Fail the release gate if q4 retrieval quality or resource criteria above fail; do not silently switch precision by hardware. Record evidence in this plan. | | |

### GOAL-002: Validate locked SQLite/FTS5/scalar architecture

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Build judged fixtures plus generated 1k/10k/100k stores with realistic topic, summary, content, scope, importance, expiry, and deletion distributions. | | |
| TASK-006 | Benchmark regular-table `vec_distance_cosine` and NumPy fallback for exact parity, ingest, DB size, latency, and extension loading; benchmark weighted FTS5 on the same stores. | | |
| TASK-007 | Exercise query-time expiry, restart cleanup, restore/reinforce, crash recovery, migration lock, WAL readers/writers, and bounded busy retries from independent processes. | | |
| TASK-008 | Run the recorded `main` worktree oracle only to explain intentional ranking/migration differences; record Recall@5/MRR/nDCG@10 and approve the embedded gate without adding Redis to `v0.11.0`. | | |

### GOAL-003: Superseded dual-backend extraction

The original dual-backend phase would preserve coupling and create throwaway
work. GOAL-008 and GOAL-011 replace it with final SQLite-only protocols.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-009 | ~~Extract a store contract around Redis behavior.~~ Superseded: define final SQLite-only service protocols from desired semantics, not Redis command shapes. | — | 2026-07-31 |
| TASK-010 | ~~Refactor Redis repository to implement the new protocol.~~ Superseded: Redis production code is frozen and never implements the final protocol. | — | 2026-07-31 |
| TASK-011 | ~~Parametrize permanent contracts over Redis and embedded backends.~~ Superseded: permanent contracts target SQLite and deterministic fakes; Redis is a temporary migration oracle only. | — | 2026-07-31 |
| TASK-012 | ~~Keep the Redis suite as the primary gate.~~ Superseded: capture legacy fixtures, then remove the Redis suite at cutover. | — | 2026-07-31 |

### GOAL-004: Superseded backend-toggle implementation

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-013 | ~~Implement a selected backend beside Redis.~~ Superseded: implement the final SQLite modules directly under `another_brain`. | — | 2026-07-31 |
| TASK-014 | ~~Mirror the Redis migration scaffold.~~ Superseded: define real versioned SQLite DDL, checksums, locks, and rollback/failure behavior. | — | 2026-07-31 |
| TASK-015 | ~~Require top-5 overlap with Redis as correctness.~~ Superseded: judged relevance and explicit desired behavior replace bug-compatible ranking. | — | 2026-07-31 |
| TASK-016 | ~~Add `STORAGE_BACKEND=redis|embedded`.~~ Superseded: SQLite is the only runtime and there is no backend flag. | — | 2026-07-31 |

### GOAL-005: Implement the locked embedding subsystem

Execute after package foundation in GOAL-009.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-017 | Implement raw ONNX Runtime CPU provider: direct `sentence_embedding`, FLOAT32 `[batch,640]`/finite/unit-norm validation, query-only prompt, lazy load, thread-safe single initialization, and health/load-error state. | | |
| TASK-018 | Download exactly the pinned q4 pair, tokenizer, and configs using temp files, resume, progress, immutable revisions, SHA-256, atomic publish, per-OS cache, and cross-process lock. | | |
| TASK-019 | Turn GOAL-001 q4 assertions into permanent slow tests; Torch/SentenceTransformers remain evaluation-only and are absent from the built wheel and final lockfile. | | |
| TASK-027 | Implement the versioned topic+summary payload builder and embedding profile validation; changing profile/input version blocks mixed search until re-embedding completes. | | |
| TASK-028 | Update `brain_remember` description, MCP instructions, schema docs, and tests to teach stable reusable topics: target 3–8, hard max 12 Harrier tokens, no catalog duplication/workflow labels/keyword stuffing. | | |
| TASK-029 | Implement one tokenizer budget validator: topic 12 without specials, final document 256 with specials, final prompted query 128 with specials, content 1,024 without specials. Reject limit+1 with actual/allowed counts; remove `CONTENT_MAX_CHARS`; no truncation/chunking. | | |

### GOAL-006: Superseded packaging/connect draft

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-020 | ~~Add an entry point without first defining the final package.~~ Superseded by GOAL-009 and GOAL-016 clean-wheel work. | — | 2026-07-31 |
| TASK-021 | ~~Use install scripts to bootstrap uv and pre-download.~~ Superseded: PyPI/uv-tool is canonical; shell scripts become thin optional helpers or are deleted. | — | 2026-07-31 |
| TASK-022 | ~~Configure harnesses with unpinned `uvx another-brain`.~~ Superseded: harnesses invoke the installed `another-brain` executable. | — | 2026-07-31 |
| TASK-023 | ~~Add doctor against the old composition root.~~ Superseded: GOAL-016 adds doctor against the final wheel and SQLite stack. | — | 2026-07-31 |

### GOAL-007: Superseded de-default-only cleanup

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-024 | ~~Keep Redis runtime code for an in-package migration command.~~ Superseded: a final legacy release exports neutral JSONL; the clean release only imports it. | — | 2026-07-31 |
| TASK-025 | ~~Retain Compose as shared deployment documentation.~~ Superseded: Docker/Redis product deployment is removed, not reclassified. | — | 2026-07-31 |
| TASK-026 | ~~Leave compose files in the final tree.~~ Superseded: GOAL-015 deletes Docker assets and references during the early clean-slate phase. | — | 2026-07-31 |

### GOAL-008: Freeze legacy behavior and approve the clean architecture

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-030 | Update `.agents/plans/another-brain-architecture.md` first: approve SQLite-only storage, separate lexical/vector/fusion modules, q4 topic+summary embeddings, durable TTL, package/CLI contract, and the external-main-oracle/early-deletion cutover. Mark Redis-era plans 01–05 superseded. | ✅ | 2026-07-31 |
| TASK-031 | In a separate worktree pinned to `main` baseline `edc0e57`, run and record the legacy unit/integration baseline; export deterministic fake-vector fixtures for identity, append-only writes, TTL, reinforce, soft-delete/restore, recent ordering, audit privacy, MCP previews, and health into backend-neutral JSON. | | |
| TASK-032 | Add desired retrieval fixtures that explicitly fix the bug: a lexical-only content identifier survives with cosine below 0.30; vector-only candidates below 0.30 do not; deleted/expired rows are absent before branch limits. | | |
| TASK-033 | Define and fixture a versioned JSONL migration envelope containing memory/audit records, IDs, identity, timestamps, metadata, remaining expiry, deletion state, and source schema version; embedding bytes are deliberately omitted. | | |
| TASK-034 | Define final Protocols in `src/another_brain/` for repository, retriever, audit, and embedding with no Redis types, score encodings, or backend selector. | | |
| TASK-035 | Record `main` baseline `edc0e57` (or the exact later maintenance-export commit) plus worktree commands as the external Redis oracle. Do not create, modify, or checkpoint Redis runtime code in `v0.11.0`. | | |

### GOAL-009: Establish the installable final package and Redis-free config

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-036 | Move runtime code under `src/another_brain/` with explicit package imports; add a build backend and `[project.scripts] another-brain = "another_brain.cli:main"`. | | |
| TASK-037 | Replace project metadata/dependencies with the final core set: `mcp`, `onnxruntime>=1.28,<1.29`, `tokenizers>=0.23,<0.24`, NumPy, `platformdirs`, pinned-compatible `sqlite-vec`, and a cross-platform file lock. Remove Redis and local Torch extras from the target lock. | | |
| TASK-038 | Implement Redis-free config with fixed retrieval/token contracts, `BRAIN_ID`, timezone/retention, optional localhost HTTP settings, and `ANOTHER_BRAIN_DATA_DIR`/`ANOTHER_BRAIN_MODEL_DIR` overrides only where operationally necessary. | | |
| TASK-039 | Resolve default paths with `platformdirs`: `brain.sqlite3` in the per-user data directory and immutable model artifacts in the per-user cache directory; create directories with user-only permissions where supported. | | |
| TASK-040 | Implement CLI shape: bare command = stdio server; `serve --http`, `model pull/status`, `doctor`, `recent`, `admin restore|hard-delete`, and `import-jsonl`. CLI startup must not import Redis, Torch, or SentenceTransformers. | | |
| TASK-041 | Build sdist/wheel with `uv build --no-sources`, install the wheel into a clean environment, run `another-brain --help`, and fail if imports resolve from the checkout instead of the installed wheel. | | |

### GOAL-010: Complete model manifest, cache, and process-local runtime

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-042 | Encode source model revision, ONNX artifact revision, q4 filenames/hashes, tokenizer/config files, prompt, dimensions, normalization, and input version in one immutable manifest consumed by installer/provider/schema. | | |
| TASK-043 | Make model installation idempotent and crash-safe: one lock per manifest, stale temp cleanup, hash before rename, and no partially installed profile visible to another process. | | |
| TASK-044 | Keep one lazy ONNX session per MCP process, serialize first load, and close references on shutdown; document measured per-process memory rather than introducing a hidden embedding daemon in the MVP. | | |
| TASK-045 | Unit-test tokenizer counts and payload bytes at every boundary, Vietnamese/English input, query/document asymmetry, output norm, corrupt/missing external data, hash mismatch, interrupted download, and concurrent installers. | | |
| TASK-046 | Expose model profile/load state through health and `model status` without loading the model merely to answer status. | | |

### GOAL-011: Implement SQLite schema, repository, lifecycle, and audit

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-047 | Implement `SQLiteConnectionFactory` with the locked PRAGMAs, extension loading per connection, context-managed close, readonly support, and NumPy fallback capability detection. | | |
| TASK-048 | Implement schema v1 exactly as specified above: migration/profile/memory/FTS/audit tables, FTS triggers, constraints, and indexes for scope, topic, catalog, recent, expiry, and deletion. | | |
| TASK-049 | Implement migration runner with checksum validation, `PRAGMA user_version`, exclusive schema transaction, concurrent-creator safety, crash rollback, and fail-fast behavior for unknown/newer versions. | | |
| TASK-050 | Implement append-only store/get/recent with brain/scope filters on every query, deterministic recent ordering, JSON metadata validation, and one atomic row+FTS commit. | | |
| TASK-051 | Implement durable TTL: compute/persist `expires_at` from importance, exclude expired rows on every read, provide bounded startup/opportunistic purge, and never renew on read. | | |
| TASK-052 | Implement reinforce, soft-delete, restore, and hard-delete transactionally: grace expiry never extends a shorter remaining TTL; restore/reinforce re-arm from importance; missing/expired/deleted semantics match the domain contract. | | |
| TASK-053 | Implement SQLite audit persistence with secret-free validation, retention cleanup, newest-first day reads, and best-effort failure isolation from the already committed memory mutation. | | |
| TASK-054 | Add focused repository contracts using temporary files, process restart, malformed rows, clock injection, boundary timestamps, rollback injection, and resource-close assertions. | | |
| TASK-055 | Add multi-process tests for simultaneous schema open, writers, readers, reinforce/forget races, busy retry exhaustion, crash recovery, and database integrity/FTS consistency checks. | | |

### GOAL-012: Rebuild BM25, vector retrieval, and RRF as separate modules

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-056 | Implement safe FTS5 query construction from Unicode terms without exposing MATCH syntax; punctuation-only input yields no lexical branch, while names/IDs/paths are tokenized predictably. | | |
| TASK-057 | Implement `SQLiteLexicalRetriever` with weighted BM25 5:3:1, mandatory brain/scope/live filters before limit, deterministic rank output, and no embedding/cosine dependency. | | |
| TASK-058 | Implement `SQLiteVectorRetriever` using scalar exact cosine over regular BLOBs after mandatory filters; convert distance to cosine consistently and apply the 0.30 vector floor before candidate rank. | | |
| TASK-059 | Implement vectorized NumPy fallback with identical filtered IDs, FLOAT32 decoding, cosine ordering, floor, and deterministic ties; expose fallback state through doctor/health without changing result semantics. | | |
| TASK-060 | Implement pure `rrf_fuse()` with equal branch weights, `k=60`, deduplication, branch evidence, candidate limits, final top-k, and the locked tie-break sequence. | | |
| TASK-061 | Implement `HybridMemoryRetriever`: run lexical/vector candidates independently, allow lexical-only results, use vector-only for no safe FTS terms, and never apply a universal post-fusion cosine gate. | | |
| TASK-062 | Add ranking tests for lexical-only identifiers, semantic-only matches, fused promotion, diacritic-insensitive Vietnamese, duplicate terms, adversarial FTS syntax, expired/deleted starvation, score-source labels, and exact sqlite-vec/NumPy parity. | | |
| TASK-063 | Run the judged 1k/10k/100k retrieval suite and record quality/latency/size gates before service cutover. | | |

### GOAL-013: Wire the final service, MCP tools, health, and transports

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-064 | Refactor `MemoryService` onto final repository/retriever/audit/embedding Protocols; remember builds topic+summary once, search embeds a bounded prompted query once, and no service import references storage implementation details. | | |
| TASK-065 | Preserve append-only diary, identity binding, previews/get separation, retention actions, and audit privacy while replacing Redis-specific health/index behavior with SQLite schema/profile/integrity state. | | |
| TASK-066 | Re-register the eight stable `brain_*` tools with final descriptions, especially reusable topic guidance, token budgets, lexical-only content behavior, and reinforce/forget trust loop. | | |
| TASK-067 | Wire composition/resource lifecycle for stdio default and optional loopback HTTP: open/close SQLite resources, lazy model lifecycle, signal handling, and health that never forces model load. | | |
| TASK-068 | Add service/tool contracts with deterministic fake embedding plus real temporary SQLite, covering every tool response and the fixed content-only retrieval behavior. | | |
| TASK-069 | Add an end-to-end subprocess test using the installed console script and an isolated data/model home: initialize, remember, search, get, reinforce, forget, restart, and verify persistence/expiry. | | |

### GOAL-014: Import neutral migration data and perform final cutover

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-070 | On a maintenance branch based on `main` (not `v0.11.0`), add/release a read-only streaming `export-jsonl` command and record its commit, version, schema, and invocation. Consume only its JSONL artifact in this branch. | | |
| TASK-071 | Implement clean-release `import-jsonl`: validate envelope/checksum/profile, preserve IDs/identity/timestamps/metadata/remaining TTL/deletion/audit state, recompute topic+summary q4 embeddings, and skip already expired records. | | |
| TASK-072 | Make import resumable/idempotent with transaction batches, conflict comparison, progress checkpoints, interruption recovery, and a final imported/skipped/failed report. | | |
| TASK-073 | Import migration fixtures produced by the external `main` worktree/export release and compare every non-embedding field, lifecycle result, lexical result, and expected re-embedded vector profile. | | |
| TASK-074 | Complete CLI, app composition, MCP server, health, and permanent tests on SQLite only; verify no backend selection or legacy runtime path has re-entered the already-clean branch. | | |
| TASK-075 | Cutover gate: clean wheel install, full permanent suite, migration import suite, judged retrieval, two-process SQLite test, restart E2E, and doctor all green on a machine/environment with no Redis or Docker installed. | | |

### GOAL-015: Early clean-slate deletion on `v0.11.0`

Execute immediately after GOAL-009, before GOAL-005/010/011/012. Legacy
comparison remains available from the external `main` worktree.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-076 | Delete Redis repositories/index/keys, Redis audit implementation, Redis config/env parsing, backend flags, Redis-only fixtures/tests, and all imports immediately after the package shell is green; retain no Redis package extra. | | |
| TASK-077 | Delete `docker/`, `.dockerignore`, Compose/Docker install and health paths, Docker-specific model/cache assumptions, and Docker instructions from scripts/product docs. | | |
| TASK-078 | Delete SentenceTransformers/Torch provider/runtime precision code, PyTorch index/source config, local extras, tests, and lockfile packages; keep fp32 reference scripts outside distributable core only if required by release evaluation. | | |
| TASK-079 | Move the backend-neutral domain/tool response code needed by the package shell, then delete superseded top-level `src/` modules/stubs and `pythonpath=["src"]` assumptions before new persistence/retrieval implementation begins. | | |
| TASK-080 | Regenerate `uv.lock` and inspect the dependency graph; fail if Redis, Torch, SentenceTransformers, CUDA, LanceDB, DuckDB, or Docker tooling remains in core/transitive runtime dependencies. | | |
| TASK-081 | Run an early zero-reference check over `src/`, permanent `tests/`, scripts, product docs, README, pyproject, and workflows for Redis/Docker/Torch runtime paths; external-oracle instructions in this plan and superseded historical plans are the only allowed references. | | |
| TASK-082 | Mark plans 03/04/05 and conflicting rules as superseded, then update AGENT_RULES/PROJECT_CONTEXT so future agents cannot reintroduce Redis/Docker or summary-only embedding behavior. | | |

### GOAL-016: Final packaging, platform, footprint, and documentation gate

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-083 | Add CI wheel/build/install/E2E matrix for Windows x86_64, macOS 14+ ARM64, and Ubuntu 22.04/24.04 x86_64 on Python 3.12–3.14; test extension-disabled NumPy fallback in every required OS family. | | |
| TASK-084 | Verify Linux ARM64 and Windows ARM64 as best-effort wheel-resolution/fallback targets; report unsupported macOS Intel and musl explicitly instead of source-building silently. | | |
| TASK-085 | Implement `another-brain doctor`: package/model hashes, tokenizer/profile, SQLite open/schema/integrity/FTS/extension or fallback, isolated write/search/delete probe, paths, and actionable per-item results. | | |
| TASK-086 | Update harness connectors to invoke installed `another-brain`; add Windows-capable examples and remove Docker/Redis/uvx assumptions. | | |
| TASK-087 | Measure clean install disk, model disk, cold/warm latency, one-/two-process memory, SQLite size/retrieval at 10k/50k/100k, and startup time; enforce the success budgets or record an approved plan revision. | | |
| TASK-088 | Update root README, `docs/architecture.md`, deployment/MCP/trust docs, skill guidance, `.agents/TESTING_GUIDE.md`, and `.agents/PROJECT_CONTEXT.md` from real final commands and paths. | | |
| TASK-089 | Final release rehearsal from an empty user profile with only `uv`: install tool, configure one harness, first model install, remember/search/get/reinforce/forget, restart, doctor, uninstall, and verify no daemon/container/server prerequisite. | | |
| TASK-090 | Set plan status `done` only after the clean tree, full CI, migration evidence, artifact hashes, resource report, and documentation gate are complete. | | |

## Test Plan

### Unit

- domain validation, topic semantics, token boundaries, payload/prompt exactness;
- q4 manifest/hash/install failure paths and provider output validation;
- SQLite row mapping, migration checksums, TTL math, retries, and audit privacy;
- safe FTS query construction, lexical ranks, vector floor, NumPy parity, and
  pure deterministic RRF;
- service and MCP response contracts with fakes.

### Integration

- real temporary SQLite files with FTS5 and sqlite-vec when available;
- extension-disabled NumPy fallback using the same fixtures;
- process restart, expiry, deletion/restore, audit retention, FTS triggers,
  rollback/crash injection, migration concurrency, and integrity checks;
- two or more independent writer/reader processes;
- q4 slow tests with pinned artifacts;
- legacy JSONL export/import before Redis code deletion.

### End-to-end

- installed wheel and console script, never editable checkout imports;
- stdio MCP round trip from an isolated profile;
- optional loopback HTTP smoke test;
- fresh model cache and interrupted/concurrent download recovery;
- Windows/macOS/Linux required matrix;
- Redis/Docker absent and network disabled after model install.

### Mandatory gates

1. Architecture approval and recorded external `main` oracle.
2. Final package shell/domain tests green, then early Redis/Docker/Torch deletion.
3. q4 quality/resource gate before release cutover.
4. SQLite/retrieval/concurrency gate before service cutover.
5. Clean-wheel/migration-import/E2E and zero-reference/platform/docs gate before release.

## Assumptions

- The clean release is allowed to break direct compatibility with Redis-backed
  runtime configuration; data compatibility is provided through versioned
  JSONL export/import, not an in-package Redis backend.
- The Redis-enabled exporter is produced, if needed, from a maintenance branch
  based on `main`; its source/dependencies never enter `v0.11.0`. The clean
  branch consumes only versioned JSONL fixtures/artifacts.
- No zero-downtime migration is required; this is a local trusted-user tool.
- The database is shared by independent local stdio processes, but the ONNX
  session remains process-local in the MVP. The measured memory cost is an
  explicit release metric; no hidden local embedding daemon is introduced.
- HTTP remains optional and loopback-only; it is not required for install or
  normal stdio use.
- `sqlite-vec` is pinned behind a small adapter because its API is pre-1.0;
  inability to load it selects the exact NumPy fallback, not installation
  failure or a source build.
- Historical approved plans remain in git as superseded records; the final
  architecture plan, product docs, code, and agent rules are authoritative.
