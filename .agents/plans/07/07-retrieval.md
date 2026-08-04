---
status: draft
created: 2026-08-04
last_updated: 2026-08-04
parent: .agents/plans/07-multiplatform-embedded-runtime.md
covers: GOAL-012, GOAL-002 (TASK-008)
---

# Sub-plan 07.07 — Lexical, vector, RRF retrieval (GOAL-012 + TASK-008)

## Summary

Rebuild retrieval as separate modules: safe FTS5 query construction, weighted
BM25 lexical branch, exact cosine vector branch (sqlite-vec with NumPy
fallback), pure deterministic RRF, and the hybrid orchestrator. The retrieval
contract (weights 5:3:1, candidate_limit 50, top_k 5, cosine floor via
micro-cosine `>= 300000`, RRF k=60, tie-break sequence, parity rules) is locked
in the master plan and fixes the legacy universal-cosine-gate bug.

Module targets: `retrieval/query.py`, `lexical.py`, `vector.py`, `fusion.py`,
`service.py`.

## Tasks

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-056 | Safe FTS5 query construction from Unicode terms without exposing MATCH syntax; punctuation-only input → no lexical branch; names/IDs/paths tokenized predictably. | | |
| TASK-057 | `SQLiteLexicalRetriever`: BM25 weights 5:3:1, mandatory brain/scope/live filters before limit 50, order `bm25 ASC,memory_id ASC`, one-based ranks, no embedding dependency. | | |
| TASK-058 | Scalar exact cosine over filtered regular BLOBs: reject malformed/non-finite, integer micro-cosine via Python half-even `round(score*1_000_000)`, floor 300000, rank by `cosine_key DESC,memory_id ASC`. | | |
| TASK-059 | Forced/vectorized NumPy fallback with identical filtered IDs, FLOAT32 decode, canonical key/floor/order; fallback state exposed via doctor/health without semantic drift. | | |
| TASK-060 | Pure `rrf_fuse()`: equal branch weights, k=60, dedupe, branch evidence, fixed 50-candidate inputs, final top-5, locked tie-break (fused desc, branch count desc, best rank asc, `memory_id` asc). | | |
| TASK-061 | `HybridMemoryRetriever`: independent branches, lexical-only results allowed, vector-only when no safe FTS terms, never a universal post-fusion cosine gate. | | |
| TASK-062 | Ranking tests: lexical-only identifiers, semantic-only matches, fused promotion, VI diacritics, duplicate/adversarial terms, live-filter starvation, source labels, canonical floor boundaries (0.299998/0.300000/0.300002), ties, malformed vectors, exact sqlite-vec/NumPy candidate/order/RRF parity within `1e-6` raw tolerance. | | |
| TASK-063 | Judged 1k/10k/50k/100k retrieval suite; emit quality/latency/size/parity evidence manifests (100 warmups, 1000 measured, 5 repetitions, pooled + per-run p50/p95/p99) before service cutover. | | |
| TASK-031 | (Deferred from GOAL-008, approved revision 2026-08-04.) In a worktree pinned to `main` baseline `edc0e57`, in the same environment setup as TASK-008: run and record the legacy unit/integration baseline; export deterministic fake-vector fixtures (identity binding, append-only writes, TTL, reinforce, soft-delete/restore, recent ordering `created_at DESC,memory_id ASC`, audit privacy, MCP previews, health) into backend-neutral JSON under `tests/fixtures/legacy-baseline/` in the `v0.11.0` branch. | | |
| TASK-008 | Run the pinned `main` worktree oracle on the same judged fixtures; record Recall@5/MRR/nDCG@10 and intentional ranking differences; enforce locked embedded thresholds without adding Redis to `v0.11.0`. | | |

## Test Plan

- Unit: query construction escapes, RRF determinism/tie-breaks, micro-cosine
  rounding boundaries, fallback equivalence.
- Integration: both vector adapters on the same fixtures must produce exact
  candidate IDs/order/ranks/RRF output.
- Evidence: TASK-063/008 manifests validate against the schema from 07.04.

## Assumptions

- `sqlite-vec` is pre-1.0 and pinned behind a small adapter; load failure
  selects the exact NumPy fallback, never a source build or install failure.
- Live filtering happens in the mandatory join, not by deleting rows from FTS.
