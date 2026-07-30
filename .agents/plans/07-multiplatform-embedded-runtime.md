---
status: draft
created: 2026-07-29
last_updated: 2026-07-29
---

# Plan 07 — Multi-platform embedded runtime (no Docker, no Redis by default)

## Summary

Make another-brain installable and runnable on Windows/macOS/Linux with a
single command — no Docker, no Redis server, no torch. Redis stays as an
optional backend for shared-server deployments; the default becomes a fully
embedded runtime:

- **Storage**: embedded (LanceDB *or* SQLite+sqlite-vec+FTS5 — decided by
  GOAL-002 spike), data under the per-OS user dir.
- **Embedding**: `onnxruntime` + `tokenizers` running
  `onnx-community/harrier-oss-v1-270m-ONNX` (int8 quantized, 344 MB +
  20 MB tokenizer). fp32/fp16/q4 selectable later.
- **Install**: publish to PyPI, `uv tool install another-brain`, harnesses
  connect over MCP **stdio** (spawned by the harness); HTTP localhost mode
  kept for shared use.

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

Target install footprint: ~0.5–0.6 GB total (int8 model + native wheels),
vs ~3–4 GB today (torch CPU + Redis image + model).

Success criteria:

1. Fresh machine with only `uv` installed: one command installs, harness
   connects via stdio, `brain_remember` → `brain_search` round-trips on
   Windows, macOS, Linux.
2. Same contract test suite passes on Redis backend and embedded backend.
3. ONNX int8 embeddings reach cosine parity vs current ST+torch fp32
   (threshold set in GOAL-001, expected ≥ 0.999 fp32 / ≥ 0.995 int8 on the
   eval set) and search recall on a fixed Vietnamese+English probe set is
   not worse than the current Redis+torch stack.
4. Redis/Docker path still works unchanged for shared deployments.

Ordering rule: add first, switch defaults mid-way, de-default Redis last.
Every GOAL is a separate PR, independently revertible.

## Tasks

### GOAL-001: Embedding parity spike (gate)

Decides whether ONNX runtime is viable at all and picks the default weight
precision. Pure spike — no changes to `src/`.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Script `spikes/embedding_parity.py`: load current ST+torch Harrier fp32 and `onnxruntime`+`tokenizers` ONNX (fp32 and int8); hand-rolled last-token pooling over attention mask + L2 norm; prepend the `web_search_query` prompt string taken from the source repo's `config_sentence_transformers.json` for queries. | | |
| TASK-002 | Eval set: ~30 strings, mixed Vietnamese (with/without diacritics) and English, short queries + longer passages, including the BM25-sanitizer edge cases. | | |
| TASK-003 | Measure per-string cosine(ST, ONNX-fp32) and cosine(ST, ONNX-int8); record min/mean; measure encode latency and RSS for both runtimes. | | |
| TASK-004 | Decision record: default precision = int8 if min cosine ≥ 0.995 vs ST fp32, else fp32; abort path documented if parity fails. | | |

### GOAL-002: Embedded storage spike (gate)

Decides LanceDB vs SQLite+sqlite-vec+FTS5. Spike only, judged on evidence.

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-005 | Probe corpus: reuse GOAL-001 strings + ~200 synthetic memory records (Vietnamese/English topics) with realistic importance/TTL distribution. | | |
| TASK-006 | LanceDB probe: hybrid search (FTS BM25 + vector, built-in RRF), Vietnamese tokenizer behavior with and without diacritics, wheel size/availability on win-amd64 + macos-arm64 + linux-x86_64, data-dir layout, concurrent readers. | | |
| TASK-007 | SQLite probe: sqlite-vec for KNN + FTS5 (unicode61 tokenizer) for BM25, app-layer RRF fusion, same Vietnamese checks, `removed`/`deleted_at` filtering, TTL sweep strategy. | | |
| TASK-008 | Compare against Redis FT.HYBRID as reference on the same corpus: top-5 overlap per query, ranking quality on diacritic-stripped queries; record decision + fallback in `.agents/decisions/`. | | |

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
| TASK-017 | Implement `EmbeddingProvider` Protocol: `onnxruntime` session + `tokenizers` tokenizer, last-token pooling, L2 norm, query prompt prepend; lazy load in worker thread like `LocalEmbeddingProvider`; `load_error`/health semantics identical. | | |
| TASK-018 | Model installer: download only the needed files (chosen precision + tokenizer + config) from `onnx-community/harrier-oss-v1-270m-ONNX` with resume + progress; per-OS cache dir; extend `ModelRegistry`/policy for the onnx source. | | |
| TASK-019 | Parity assertions from GOAL-001 become a permanent test (marked slow, downloads on first run); `EMBEDDING_PROVIDER=onnx|local` config with onnx as default; torch stays in the `local` extra only. | | |

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
- End-to-end: fresh-profile smoke test per OS in CI (install → stdio
  connect → remember → search → forget) at TASK-020.
- Existing suite must stay green at every GOAL boundary.

## Assumptions

- Redis is never deleted from the codebase; it becomes opt-in. Compose
  files stay for shared-server deployments.
- Default embedding precision is int8 (344 MB) pending GOAL-001 evidence;
  fp32 remains selectable.
- Publishing to PyPI is in scope; the exact package name is resolved at
  TASK-020.
- Auth model unchanged: trusted local agents, no HTTP exposure on
  untrusted networks.
- Existing Redis data migration is best-effort export/import; no
  zero-downtime requirement (single-user local tool).
- stdio is the default transport after GOAL-006; HTTP localhost remains
  for multi-agent/team setups.
