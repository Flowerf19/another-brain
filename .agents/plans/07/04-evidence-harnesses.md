---
status: done
created: 2026-08-04
last_updated: 2026-08-05
parent: .agents/plans/07-multiplatform-embedded-runtime.md
covers: GOAL-001, GOAL-002 (TASK-005..007)
---

# Sub-plan 07.04 — Q4 evidence and reusable harnesses (GOAL-001 + GOAL-002 harnesses)

## Summary

Produce the locked q4 quality/resource evidence and the reusable
benchmark/concurrency harnesses before the real subsystems exist. The corpus,
thresholds, evidence manifest schema, and accepted concurrency workload are
fully specified in the master plan's "Verification contracts" — they are
acceptance criteria, not adjustable in test code.

## Tasks

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-001 | Create isolated `spikes/fp32/` project (own `pyproject.toml`, frozen `uv.lock`, Python 3.12 CPU) with locked fp32 revision `31de22b673913c7d658c0f03f792d77c2dcf8ebd` and `model.safetensors` SHA-256 `90933b68...cb51a`; compare against raw ONNX q4 using each profile's pinned tokenizer, direct `sentence_embedding`, query-only prompt. Not a workspace member, not in root lock/wheel. | ✅ | 2026-08-04 |
| TASK-002 | Build and checksum the `embedding-quality-v1` corpus: 600 documents, 120 judged queries (60 VI / 60 EN; 40 per token bucket 1–16/17–64/65–128; 20 VI no-diacritic), graded 0..3 with ≥1 relevant + 4 hard negatives per query, plus the 24-case behavior partition (12 content-only identifiers, 6 punctuation-only, 6 expired/deleted starvation). Manifest records all locked hashes/versions/seeds. | ✅ | 2026-08-04 |
| TASK-003 | Emit reproducible evidence manifest + raw samples for cosine(q4,fp32), Recall@5, MRR, nDCG@10, cold/warm latency by token bucket, steady/peak RSS, one-/two-process PSS per the manifest schema (run ID, UTC, commit/dirty hash, command, environment, versions+hashes, PRAGMAs, thresholds, per-threshold result). | ✅ | 2026-08-04 |
| TASK-004 | Enforce the Q4 thresholds above (revised 2026-08-04 per approved decision: median cosine ≥0.98, p5 ≥0.97; macro Recall@5 ≥0.90, MRR ≥0.80, nDCG@10 ≥0.83; q4≤fp32 deltas 0.02/0.03; resource budgets — evidence: `spikes/fp32/reports/q4-gate-2026-08-04.md`). The 24-case behavior partition is deferred to the GOAL-012 gate (TASK-062). On failure: stop or record an approved plan revision. | ✅ | 2026-08-04 |
| TASK-005 | Build checksummed judged fixtures plus deterministic 1k/10k/50k/100k stores (realistic text/scope/importance/expiry/deletion distributions, recorded seeds); write and checksum `benchmarks/reference-machine.json` before any performance run. | ✅ | 2026-08-04 |
| TASK-006 | Benchmark regular-table `vec_distance_cosine` vs forced NumPy fallback and weighted FTS5 on those stores; enforce canonical candidate/order parity (`abs <= 1e-6` raw-score tolerance, exact IDs/order/ranks/RRF); emit ingest/DB-size/latency/extension manifests. | ✅ | 2026-08-05 |
> Closed 2026-08-05. The vector half landed with TASK-063 (both adapters,
> parity per the approved revision in 07.07). The weighted-FTS5 half was
> missing — `run_suite.py` measured only `vector_branch` and `hybrid_search`
> — and is now a first-class `lexical_branch` series measured once per store
> under the same protocol (BM25 carries no embedding dependency, so it is
> identical under both vector backends).
>
> **The lexical branch is the slowest one at scale and carries no budget.**
> p95 by store: 3.12 / 8.90 / 35.06 / 71.68 ms, versus sqlite-vec 1.85 /
> 2.98 / 7.75 / 13.53 and NumPy 3.22 / 5.32 / 13.03 / 26.90. At 100k, BM25 is
> 5.3x the sqlite-vec branch and accounts for most of the 116.92 ms hybrid
> p95. Success criterion 9 budgets *vector* retrieval only (25/75/150 ms), so
> nothing gates the branch that actually dominates. Left unbudgeted rather
> than inventing a threshold here: SC-9 is locked in the master plan, and
> setting a lexical budget is a plan revision. Raised for GOAL-016 (TASK-087)
> where the resource envelope is re-measured — and note the FTS5 cost is
> driven by candidates surviving the scope/live filter, so it depends on how
> many rows share one scope, not on total store size.
>
> Evidence for the wired-in series is `retsuite-20260805T041805Z`, run at the
> full locked protocol (5 reps x 1000 measured, pooled n=5000) on the **10k
> store only** — lexical p50/p95/p99 4.90/8.78/9.83 ms, reproducing the
> hand-measured 8.90 ms. The 1k/50k/100k figures above are the hand
> measurements; re-run the full sweep on the reference machine before those
> numbers are cited as accepted evidence.
| TASK-007 | Implement the reusable spawned-process workload driver + allowed-outcome oracle using deterministic fake/precomputed embeddings: fresh-open storm (8 processes, 5 seeds), mixed WAL workload (2 writers/2 readers, 500 ops each, 50 hot IDs, both extension modes), crash probe, busy-exhaustion probe. Validate barriers/seeds/injection on a throwaway SQLite toy before TASK-055 applies it to the real repository. | ✅ | 2026-08-04 |

## Test Plan

- Corpus/manifest self-validation: missing field or hash mismatch invalidates.
- Harness determinism: same seed → identical operation sequence and outcomes
  across runs; barrier/injection points proven on the toy driver.
- Evidence manifests validate against the schema; invalid manifests fail CI.

## Assumptions

- Performance numbers are accepted only on the checksummed reference machine;
  dev runs elsewhere are diagnostic only.
- Fake embeddings in the concurrency harness are precomputed and deterministic;
  the harness never loads ONNX.
