---
status: draft
created: 2026-07-29
last_updated: 2026-07-31
---

# Plan 07 — Multi-platform embedded runtime (no Docker, no Redis by default)

## Summary

Make another-brain installable and runnable on Windows/macOS/Linux with a
single command — no Docker, Redis server, or torch in the normal runtime.
Redis is retained only as a temporary migration/legacy source. The default is
a fully embedded runtime:

- **Storage**: ordinary SQLite tables as the source of truth, FTS5 for weighted
  lexical retrieval, and `sqlite-vec` scalar `vec_distance_cosine` for exact
  vector retrieval. There is no `vec0`, ANN, or sidecar vector index in the
  default architecture; NumPy exact scan is the compatibility fallback.
- **Embedding**: raw `onnxruntime` CPU + `tokenizers` running the pinned
  `onnx-community/harrier-oss-v1-270m-ONNX` ordinary q4 artifact. No
  Transformers, Optimum, SentenceTransformers, or Torch belongs to core.
- **Install**: publish to PyPI, `uv tool install another-brain`, harnesses
  connect over MCP **stdio** (spawned by the harness); HTTP localhost mode is
  optional rather than part of the normal quick start.

### Locked decisions

These decisions are no longer implementation-spike choices:

1. **One canonical store**: SQLite regular tables + FTS5 + `sqlite-vec`
   scalar exact cosine. Every read/search filters durable `expires_at` and
   `deleted_at`; WAL and bounded busy retries support independent stdio
   processes.
2. **One runtime**: `onnxruntime` CPUExecutionProvider + `tokenizers`. The
   exported graph already returns normalized `sentence_embedding`
   `FLOAT32[640]`; application code must not repeat last-token pooling or L2
   normalization.
3. **One default artifact**: q4, pinned by immutable Hugging Face revisions
   and SHA-256 for both `onnx/model_q4.onnx` and its matching
   `onnx/model_q4.onnx_data`. q4f16 is not auto-selected because its FP16-heavy
   graph is less CPU-portable; int8/fp32 are evaluation references only.
4. **One vector per memory**: build one document payload as
   `topic.replace("-", " ") + "\n" + summary.strip()` and encode it without
   the query prompt. `content` is FTS5-only; `catalog`, metadata, scope, time,
   and importance remain filters/provenance. A payload change increments
   `embedding_input_version` and requires re-embedding.
5. **Topic authoring contract**: topic is a concise semantic anchor. After
   replacing hyphens with spaces, target 3-8 Harrier tokens and enforce a hard
   maximum of 12 Harrier tokens. It names the stable retrieval subject rather
   than repeating catalog, recording workflow state, or stuffing keywords.
   All text-size limits use the pinned Harrier tokenizer; there is no separate
   character-count limit. The `brain_remember` tool description and server
   instructions must teach this contract to calling models.
6. **Retrieval text fields**: weighted FTS5 covers `topic`, `summary`, and
   `content`; the initial benchmark weights are 5:3:1. Vector retrieval uses
   only the single topic+summary embedding. App-layer RRF combines the two
   ranked branches.
7. **One tokenizer-based size contract**: every text budget is counted by the
   pinned Harrier tokenizer, never by characters. Humanized topic targets 3-8
   tokens and has a hard limit of 12 without special tokens; the final
   topic+summary document payload has a hard limit of 256 including special
   tokens; query prompt+query has a hard limit of 128 including special
   tokens; lexical-only content has a hard limit of 1,024 without special
   tokens. Over-limit input is rejected with an actionable error — never
   silently truncated or automatically chunked. These are product constants,
   not environment-specific knobs in the MVP.

Verified facts this plan relies on:

- ONNX release exists with `model.onnx` (1106 MB), `model_fp16` (553 MB),
  `model_quantized` (344 MB), `model_q4` (206 MB), `model_q4f16` (172 MB),
  plus `tokenizer.json` (20 MB) — HF API, checked 2026-07-29.
- Harrier is decoder-only with last-token pooling + L2 norm, 640-dim,
  query prompt `web_search_query` (registry.py verified profile).
- `EmbeddingProvider` Protocol already exists (src/memory/embeddings.py);
  retention/TTL, soft-delete, keys, migrations are app-layer Python — only
  document storage and hybrid search are Redis-coupled.
- fastembed cannot be used (fixed model whitelist, no Harrier).

Provisional target install footprint: ~0.35–0.45 GB total (q4 graph/data,
tokenizer, runtime, and core wheels), vs ~3–4 GB today (torch CPU + Redis
image + model). GOAL-001 records clean-environment disk and loaded RSS before
this becomes a release budget.

Success criteria:

1. Fresh machine with only `uv` installed: one command installs, harness
   connects via stdio, `brain_remember` → `brain_search` round-trips on
   Windows, macOS, Linux.
2. The permanent contract suite passes on the embedded backend; temporary
   Redis fixtures remain only to verify migration parity until legacy removal.
3. ONNX q4 embeddings pass the permanent quality gate against the current
   ST+torch fp32 reference and preserve judged Vietnamese+English retrieval
   quality. q4 is the product default; the gate detects regressions rather
   than selecting a precision dynamically.
4. Redis/Docker is not required by install, startup, health, or normal use;
   the legacy code exists only long enough to support a tested migration.

Ordering rule: add first, switch defaults mid-way, de-default Redis last.
Every GOAL is a separate PR, independently revertible.

## Tasks

### GOAL-001: Embedding q4 quality validation (gate)

Validates the locked ONNX/q4 decision against the fp32 reference and records
its quality, latency, and memory envelope. It does not dynamically select a
precision. Pure spike — no changes to `src/`.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Script `spikes/embedding_parity.py`: load current ST+torch Harrier fp32 and raw `onnxruntime`+`tokenizers` q4; use the graph's `sentence_embedding` output directly; prepend the exact pinned `web_search_query` prompt only for queries. | | |
| TASK-002 | Eval set: judged Vietnamese (with/without diacritics) and English query-memory pairs, short queries, topic+summary documents, longer lexical-only content, and BM25 sanitizer edge cases. | | |
| TASK-003 | Measure cosine(q4, ST-fp32), retrieval Recall@5/MRR/nDCG@10, encode latency by token bucket, session load latency, steady/peak RSS, and two-process PSS. | | |
| TASK-004 | Acceptance record: q4 remains fixed only if the judged retrieval thresholds pass; failure blocks release and requires an explicit architecture review rather than silent precision auto-selection. | | |

### GOAL-002: Embedded SQLite validation (gate)

Validates the locked regular-SQLite + FTS5 + `sqlite-vec` scalar architecture,
including its NumPy fallback, on realistic scale and concurrent stdio access.
It no longer compares or selects a database engine.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Build a judged Vietnamese/English probe corpus plus generated 1k/10k/100k stores with realistic topic, summary, content, scope, importance, durable TTL, and soft-delete distributions. | | |
| TASK-006 | Measure regular-table `vec_distance_cosine` and NumPy fallback latency, ingest, DB size, exact-result parity, extension-load behavior, and weighted FTS5 (`topic:summary:content` initial 5:3:1) on the target platform matrix. | | |
| TASK-007 | Verify app-layer RRF, diacritic handling, exact identifiers in content, query-time expiry exclusion, restart cleanup, restore/reinforce semantics, crash recovery, and simultaneous WAL readers/writers from independent processes. | | |
| TASK-008 | Compare the locked embedded implementation against legacy Redis on the judged corpus for migration evidence only; record Recall@5/MRR/nDCG@10, ranking differences, fallback behavior, and the final acceptance result. | | |

### GOAL-003: `MemoryStore` interface + contract tests

The riskiest step: a wrong interface forces rework in both backends.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-009 | Extract the store contract from `redis_repository.py` + `memory/search.py` + `memory/service.py`: remember, get, soft-delete, reinforce (TTL renew), recent (timeline page), hybrid_search (query text + vector + filters → fused, cosine floor, deleted excluded). Document exact semantics: soft-delete exclusion, TTL behavior, score fields, BM25 sanitization responsibility. | | |
| TASK-010 | Define `MemoryStore` Protocol in `src/storage/`; refactor `RedisMemoryRepository` to implement it with zero behavior change. | | |
| TASK-011 | Contract test suite in `tests/contract/` runnable against any backend (fixture parametrized by backend factory); port the behavioral assertions from existing repository/search tests. | | |
| TASK-012 | Contract suite green on Redis backend; full existing test suite green. | | |

### GOAL-004: Embedded storage backend

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-013 | Implement the GOAL-002 winner as a new `MemoryStore` implementation under `src/storage/`; recreate the Vietnamese-aware BM25 sanitization semantics (`_BM25_STRIP_RE` behavior) appropriate to the backend's tokenizer. | | |
| TASK-014 | Migrations equivalent: schema creation/version check on open, fail-fast on version mismatch (mirror `storage/migrations.py`). | | |
| TASK-015 | Contract suite green on the embedded backend; top-5 overlap check vs Redis on the GOAL-002 corpus re-run through the real service layer. | | |
| TASK-016 | `STORAGE_BACKEND=redis|embedded` config; embedded is default, data dir resolution per-OS (`~/.another-brain` / `%LOCALAPPDATA%\another-brain`), `BRAIN_ID` namespacing preserved. | | |

### GOAL-005: `OnnxEmbeddingProvider`

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-017 | Implement `EmbeddingProvider` with raw `onnxruntime` CPU + `tokenizers`: consume graph output `sentence_embedding` directly, validate FLOAT32 `[batch, 640]`, finite values and unit norm, prepend the pinned query prompt only for queries, and lazy-load in a worker thread with explicit `load_error`/health semantics. | | |
| TASK-018 | Model installer: download exactly the pinned q4 graph, matching external data file, tokenizer and configs with resume, progress, SHA-256 verification, per-OS cache layout, and a cross-process download lock. | | |
| TASK-019 | q4 quality assertions from GOAL-001 become permanent slow tests; ONNX is the only core provider and torch/SentenceTransformers remain migration-time test references rather than an installed runtime. | | |
| TASK-027 | Add a versioned document-payload builder that produces one embedding from humanized topic + newline + stripped summary; persist and validate the active model revision, q4 variant, dimension, output dtype, and `embedding_input_version`. Re-embed when this contract changes. | | |
| TASK-028 | Update `brain_remember` tool description, MCP server instructions, schemas, and tests to teach calling models the topic contract: target 3-8 and hard-limit 12 Harrier tokens after humanizing the slug; use a stable semantic subject rather than a generic label, catalog duplicate, workflow state, or keyword list. State explicitly that topic+summary forms one vector and content is lexical-only. | | |
| TASK-029 | Add one tokenizer-based input-budget validator and replace `CONTENT_MAX_CHARS`: topic hard max 12 without special tokens; final topic+summary payload max 256 with special tokens; final prompt+query max 128 with special tokens; content max 1,024 without special tokens. Reject over-limit input without truncation/chunking, report actual/allowed tokens, keep these as fixed MVP contract constants, and add exact boundary tests. | | |

### GOAL-006: Packaging, install, connect

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-020 | Check PyPI name availability (`another-brain` or fallback), entry point `another-brain` (stdio default, `serve --http` for server mode), CI matrix test on windows/macos/linux. | | |
| TASK-021 | New install flow: `install.sh` + `install.ps1` bootstrap `uv` then `uv tool install`; model pre-download with progress during install (`MODEL_DOWNLOAD_POLICY=on_start` preserved). | | |
| TASK-022 | Rewrite `connect.sh` payloads to stdio `{command: "uvx", args: ["another-brain"]}` per harness; add Windows-capable connect (Python or ps1). | | |
| TASK-023 | `another-brain doctor`: checks model present, embeds one sentence, opens store, writes/reads one record, reports per-item OK/FAIL. | | |

### GOAL-007: De-default Redis/Docker + migration

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-024 | Data migration tool: export from Redis brain → import into embedded store (documented command; required only for existing users with data to keep). | | |
| TASK-025 | Docs flip: README quick start becomes uvx + stdio; `docs/deployment.md` keeps compose as "shared server deployment"; PROJECT_CONTEXT and architecture plan updated (architecture-docs). | | |
| TASK-026 | `scripts/install.sh` no longer touches Docker; compose files remain but are no longer referenced by the default path. | | |

## Test Plan

- GOAL-001/002 spikes produce recorded numbers (cosine parity, top-5
  overlap, latency/RSS) — these are the acceptance evidence for the two
  gate decisions.
- Contract suite (GOAL-003) is the permanent regression net: parametrized
  over `redis` and `embedded`, covering remember/get/forget/grace-period,
  reinforce TTL renewal, recent ordering, hybrid search ranking, deleted
  exclusion, cosine floor.
- Parity test (TASK-019) guards embedding drift per release.
- Token-budget tests cover exact-limit acceptance and limit+1 rejection for
  topic, final document payload, final prompted query, and content; they also
  assert that no truncation or chunking occurs.
- End-to-end: fresh-profile smoke test per OS in CI (install → stdio
  connect → remember → search → forget) at TASK-020.
- Existing suite must stay green at every GOAL boundary.

## Assumptions

- Redis is not a normal runtime backend. It remains only as a temporary,
  opt-in migration/legacy reader and is excluded from core dependencies.
- The default embedding artifact is the pinned ordinary q4 pair; there is no
  hardware-dependent precision auto-selection.
- Publishing to PyPI is in scope; the exact package name is resolved at
  TASK-020.
- Auth model unchanged: trusted local agents, no HTTP exposure on
  untrusted networks.
- Existing Redis data migration is best-effort export/import; no
  zero-downtime requirement (single-user local tool).
- stdio is the default transport after GOAL-006; HTTP localhost remains
  for multi-agent/team setups.
