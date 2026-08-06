# Benchmark & spike evidence record

Single consolidation (2026-08-06) of every performance/quality report produced
during the Plan 07 rebuild. Originals lived under `benchmarks/reports/`,
`benchmarks/evidence/`, and `spikes/fp32/`; those directories were retired
once the permanent gates moved into `tests/` (originals remain in git
history). Sections below preserve the decisive numbers verbatim; run
manifests cited by filename are also in git history.

**Reference machine** (`benchmarks/reference-machine.json`, sha256
`c15ac95e…`): AMD Ryzen AI 9 HX 370 (13 physical / 24 logical cores), 31 GiB
RAM, Linux 7.1.5-201.fc44.x86_64, governor `powersave`, CPython 3.14.6,
onnxruntime defaults (intra_op = physical cores, inter_op = 1). Cold load =
fresh process; no OS page-cache drop.

## 1. Q4 embedding quality/resource gate (GOAL-001, TASK-001..004)

Corpus `embedding-quality-v1` (sha256 `71aff90284f14d0d…`): 624 docs, 120
judged queries (60 VI / 60 EN, buckets 40/40/40, 20 VI no-diacritic), seed
20260804. q4 target `onnx-community/harrier-oss-v1-270m-ONNX` @ `d59c919d…`
(5 files SHA-256 verified, `model_q4.onnx_data` 205.5 MB) on the production
stack (onnxruntime 1.28.0, tokenizers 0.23.1); fp32 oracle
`microsoft/harrier-oss-v1-270m` @ `31de22b6…` (`model.safetensors` 536.2 MB,
SHA-256 `90933b68…cb51a`) on torch 2.13.0+cpu / ST 5.6.1 / tokenizers 0.22.2
in an isolated spike env. Payloads input version 2, byte-identical both
profiles (`default_prompt_name: null` verified).

### Environment split (recorded deviation)

transformers 5.x caps `tokenizers<=0.23.0` and tokenizers 0.23.0 was never
released, so no single env hosts SentenceTransformers AND production
tokenizers 0.23.1. The fp32 oracle therefore ran in the spike env while q4
ran under the root lock; tokenizer artifacts are hash-pinned per profile, so
measured parity includes tokenizer-conversion differences.

### Gate history: FAIL → approved revision → PASS

First full run `q4gate-20260804T072033Z`: **FAIL — 11/13 thresholds**:

| # | Threshold | Measured | Gate | Result |
|---|-----------|----------|------|--------|
| 1 | paired cosine(q4, fp32) median | 0.9808 | ≥ 0.99 | ❌ FAIL |
| 2 | paired cosine(q4, fp32) p5 | 0.9704 | ≥ 0.97 | ✅ |
| 3 | q4 macro Recall@5 | 0.9317 | ≥ 0.90 | ✅ |
| 4 | q4 macro MRR | 0.9431 | ≥ 0.80 | ✅ |
| 5 | q4 macro nDCG@10 | 0.8380 | ≥ 0.85 | ❌ FAIL |
| 6 | Δ Recall@5 (fp32−q4) | 0.0017 | ≤ 0.02 | ✅ |
| 7 | Δ MRR | −0.0007 | ≤ 0.02 | ✅ |
| 8 | Δ nDCG@10 | 0.0133 | ≤ 0.02 | ✅ |
| 9 | Δ Recall@5 within language | 0.0033 | ≤ 0.03 | ✅ |
| 10 | Δ MRR within language | 0.0015 | ≤ 0.03 | ✅ |
| 11 | Δ nDCG@10 within language | 0.0203 | ≤ 0.03 | ✅ |
| 12 | warm p95 latency ≤128 tokens | 68.7 ms (bucket 2; buckets 0–1 lower) | ≤ 100 ms | ✅ |
| 13 | steady RSS | 412 MiB (peak 437 MiB) | ≤ 500 MiB | ✅ |

Supplementary: cold load 0.78–0.86 s over 10 fresh processes; two-process
PSS ≈ 318 MiB each. Per-language (q4 vs fp32): VI 0.863/0.886/0.767 vs
0.867/0.885/0.787; EN 1.000/1.000/0.909 vs 1.000/1.000/0.915
(Recall@5/MRR/nDCG@10).

Failure analysis: (1) 4-bit weight quantization shifts embeddings ~2% from
fp32 in absolute cosine, but every retrieval delta passes with wide margin
and q4 matches or beats fp32 on MRR — ranking parity holds. (2) Absolute
nDCG is corpus-difficulty dependent; the VI partition (0.767) drags the
macro, 20/60 VI queries being no-diacritic variants, and fp32 on the same
corpus is only 0.851 with a q4↔fp32 delta of just 0.0133.

**Resolution (2026-08-04, maintainer-approved): Option B.** Thresholds
revised with this run as evidence: paired cosine median `>=0.99 → >=0.98`,
q4 macro nDCG@10 `>=0.85 → >=0.83`. Locked artifact stays `model_q4.onnx`.
Re-run after the revision: **gate PASS 13/13**, manifest
`manifest-q4gate-20260804T075759Z.json`.

### Permanent product gate (TASK-019) — PASS 2026-08-04

`tests/integration/test_q4_embedding_gate.py` (slow, skips when the pinned
profile is absent) against the real q4 profile, 600 docs + 120 queries,
single-item encodes, 22.8 s: macro Recall@5 **0.9317** (≥0.90), MRR
**0.9431** (≥0.80), nDCG@10 **0.8380** (≥0.83) — reproduces the spike gate
manifest exactly; product provider, corpus, and metric formulas are
byte-consistent with the spike pipeline. The paired fp32 cosine thresholds
stay evaluation-only; only the q4 profile is asserted permanently. (Smoke
parity signal recorded beforehand: 8 probes median cosine 0.9806, min
0.9785 — the early warning that the corpus median would land ~0.98.)

## 2. Retrieval suite (TASK-063 gate, sqlite-vec vs NumPy fallback)

Judged corpus on the v1 SQLite schema, fused top-10 (RRF k=60), vector
branch via sqlite-vec or NumPy fallback. Parity contract: raw |Δ| ≤ 1e-6
passes (max 9.48e-07); exact-canonical comparison differs in 120 cases
(recorded; canonicalization is half-even micro-cosine, floor 300000).

Run `retsuite-20260804T132446Z` (1k + 10k): **FAIL** — hybrid p95 862–1247 ms
(FTS5 query builder regression; fixed, see §3). Run
`retsuite-20260804T133753Z` (1k + 10k): **PASS** after the fix — 10k vector
p95 3.04 ms (sqlite-vec) / 5.54 ms (numpy), hybrid p95 11.41/13.04 ms.

Full-scale run `retsuite-20260804T133909Z`: **PASS** on all locked thresholds.

| Rows (DB size) | Backend | Recall@5 | MRR | nDCG@10 | vector p50/p95/p99 (ms) | hybrid p95 (ms) |
|---|---|---|---|---|---|---|
| 1k (4.2 MB) | sqlite-vec | 0.9800 | 0.9958 | 0.8843 | 1.46/1.91/2.46 | 4.73 |
| 1k | numpy | 0.9800 | 0.9958 | 0.8843 | 2.48/3.08/3.63 | 5.90 |
| 10k (38.6 MB) | sqlite-vec | 0.9700 | 0.9958 | 0.8837 | 2.54/3.32/4.06 | 11.65 |
| 10k | numpy | 0.9700 | 0.9958 | 0.8837 | 4.29/5.62/7.77 | 14.16 |
| 50k (192.1 MB) | sqlite-vec | 0.9717 | 0.9958 | 0.8787 | 9.01/13.79/15.48 | 59.11 |
| 50k | numpy | 0.9717 | 0.9958 | 0.8787 | 14.30/20.89/24.60 | 60.29 |
| 100k (382.7 MB) | sqlite-vec | 0.8567 | 0.9958 | 0.7129 | 18.52/25.49/34.99 | 116.92 |
| 100k | numpy | 0.8567 | 0.9958 | 0.7129 | 22.88/35.84/40.09 | 108.86 |

Confirmation run `retsuite-20260805T041805Z` (10k): **PASS**, plus
backend-independent FTS5-lexical latency p50/p95/p99 4.90/8.78/9.83 ms.

## 3. SQLite vector / FTS5 diagnostic benchmark (TASK-005/006, 2026-08-04)

Stores `brain-bench-{1k,10k,50k,100k}.sqlite3` (3.8/36.7/177.7/351.2 MB),
seed 20260804+size, 624 corpus docs + seeded fillers, 5% expired + 5%
soft-deleted; filter under test = brain + live (worst-case scan). Reduced
protocol (full 5×1000-run deferred to the §2 gate after a thermal incident).

| Store | vec (sqlite-vec) | NumPy fallback | FTS5 BM25 5:3:1 | Budget (vec) |
|-------|------------------|----------------|-----------------|--------------|
| 10k (probe) | 6.4 ms | 26.5 ms | — | ≤ 25 ms ✅ |
| 10k (partial) | 8.2 ms | 44.6 ms | 16.5 ms | ≤ 25 ms ✅ |
| 100k (probe) | 30.3 ms | 231.3 ms | — | ≤ 150 ms ✅ |

Verdicts: (1) sqlite-vec meets budgets at every measured size with wide
margin. (2) NumPy fallback exceeds budgets at scale (231 ms @100k) —
acceptable compatibility path, but the release notes must state fallback p95
openly (TASK-087). (3) FTS5 was slower than vector scan at 10k (16.5 vs
8.2 ms) under ~40-term OR queries with heavy template-term repetition;
action folded into TASK-056/057 (safe FTS term capping) — the regression
behind the first §2 run's 862–1247 ms hybrid p95.

Incident: the first full-protocol attempt was manually aborted at **90 °C**
(≈16 min sustained NumPy load at 100k); maintainer ruled existing data
sufficient and the full locked protocol ran later under thermal pacing.

## 4. Legacy oracle comparison (TASK-008, 2026-08-04)

Oracle: pinned `main@edc0e57`, Redis 8.8, legacy fp32 pipeline (ST Harrier,
summary-only unprompted documents, prompted queries, one `FT.HYBRID` in-Redis
RRF k=60 top_k=20 + universal cosine gate ≥0.30 on every hit). Same judged
corpus both sides; clean side = v1 SQLite, q4 topic+summary (input v2), RRF
k=60, cosine floor 0.30 on vector candidates only. Seed 33.6 s; legacy
search mean 58.7 ms/query.

| metric | legacy oracle | clean embedded | locked threshold | clean passes |
|--------|---------------|----------------|------------------|--------------|
| Recall@5 | 0.9000 | 0.9783 | ≥ 0.90 | ✅ |
| MRR | 0.9205 | 0.9958 | ≥ 0.80 | ✅ |
| nDCG@10 | 0.7983 | 0.8824 | ≥ 0.83 | ✅ |

Clean beats legacy on every aggregate (+0.078 recall, +0.075 MRR, +0.084
nDCG). Recorded intentional ranking differences: (1) the legacy universal
cosine gate drops BM25-only hits — a bug proven by the deterministic fixture
`tests/fixtures/legacy-baseline/behavior-v1.json`
(`legacy-cosine-gate-drops-content-match`), which the corpus alone does not
trigger; (2) document payload summary-only vs topic+summary input v2 — why
clean recall wins on identifier/topic queries; (3) fusion in-Redis (top-20
window) vs app-layer (candidate_limit=50/branch, final top_k=5, nDCG on
fused top-10); (4) recent tie-break `period_start DESC`-only vs locked
`created_at DESC, memory_id ASC`; (5) raw float vs canonical micro-cosine
half-even scores.

Behavior partition (24 cases): 12 content-only identifiers — legacy top-20,
clean rank 1 via lexical branch (fused rank 7–9 when live-tail docs collide
on the shared `runid` token family); 6 punctuation-only — legacy degrades to
KNN-only, clean runs vector-only without error; 6 expired/deleted
starvation — both stacks exclude the stale row.

## 5. Concurrency harness validation (TASK-007, 2026-08-04) — PASS 61/61

Spawned-process driver + allowed-outcome oracle against a toy store
mirroring the locked production patterns (WAL, busy_timeout, cross-process
schema lock via filelock, `BEGIN IMMEDIATE` + bounded busy retry, FTS5
external content with triggers, live filtering). Quick + full protocol:

| Scenario | Parameters | Result |
|----------|-----------|--------|
| Fresh-open storm | 8 procs × 5 seeds, barrier-synced open of absent DB | 8/8 opened, one schema version `[1]`, <1 s/storm |
| Mixed WAL workload | preseed 500, 4 procs × 500 ops, 50 hot IDs, 5 seeds | zero unhandled errors/fatals, integrity ok, FTS trigger parity, no duplicate migrations |
| Crash probe | SIGKILL one writer mid-run, reopen, second workload | integrity ok after crash, post-crash workload clean |
| Busy-exhaustion probe | external `BEGIN IMMEDIATE` held past retry envelope | typed `busy_exhausted` in 1.7 s, no hang, integrity ok |

Bugs found and fixed during validation: forkserver start method (Python
3.14) dropped parent-side retry overrides → retry envelope moved into
`WorkloadConfig` pickling; join envelope let a busy-waiting child outlive
`join(timeout)` → driver computes a `join_timeout_s` deadline and terminates
survivors. TASK-055 re-used this driver against the real repository (both
vector backends, 500 ops/worker, 5 seeds, full post-run oracle).

## 6. Per-process embedding memory (TASK-044, 2026-08-04)

Product `ONNXEmbeddingProvider` in one fresh process, RSS/PSS from
`/proc/self/smaps_rollup`, real pinned q4 files:

| Point | RSS MiB | PSS MiB |
|-------|--------:|--------:|
| interpreter baseline | 52.4 | 40.3 |
| provider constructed (NOT_LOADED) | 52.4 | 40.3 |
| loaded, first embed | 374.7 | 361.7 |
| warm embed | 375.1 | 362.1 |
| after `close()` | 354.9 | 342.7 |

Readings: constructing the provider loads nothing (matches the TASK-046
contract); one session costs **~322 MiB RSS / ~321 MiB PSS** — the
per-process budget to publish at the release gate (TASK-087), consistent
with the §1 resource gate (412 MiB steady RSS includes eval scaffolding);
`close()` releases only ~20 MiB (onnxruntime arena; real reclamation at
process exit); no hidden embedding daemon — one lazy session per MCP
process, serialized first load.
