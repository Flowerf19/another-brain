# Legacy oracle comparison (TASK-008) — 2026-08-04

## Setup

- Oracle: pinned `main@edc0e57` worktree (`../another-brain-main`), Redis 8.8
  (docker, host port 1906), legacy venv `uv sync --extra local`
  (torch 2.13.0+cpu, sentence-transformers 5.6.0, redis-py 8.0.1).
- Legacy pipeline: SentenceTransformers fp32 Harrier
  (`microsoft/harrier-oss-v1-270m`, cached offline); documents embed
  **summary-only, unprompted**; queries prompted (`web_search_query`);
  search = one Redis `FT.HYBRID` (in-Redis RRF k=60, top_k=20) followed by
  the app-layer **universal cosine gate ≥ 0.30 on every hit**.
- Judged corpus: `embedding-quality-v1` (624 docs, 120 queries) seeded into
  Redis with the legacy embedding; expired rows evicted via key TTL,
  soft-deleted rows flagged. Clean side: the same corpus on the v1 SQLite
  schema with q4 topic+summary embeddings (input version 2), fused top-10
  (RRF k=60, cosine floor 0.30 on vector candidates only).
- Artifact: `benchmarks/evidence/legacy-oracle-judged-20260804T135723Z.json`;
  seed 33.6 s, legacy search mean 58.7 ms/query.

## Judged quality

| metric    | legacy oracle | clean embedded | locked threshold (Q4 gate) | clean passes |
|-----------|---------------|----------------|----------------------------|--------------|
| Recall@5  | 0.9000        | 0.9783         | ≥ 0.90                     | ✅           |
| MRR       | 0.9205        | 0.9958         | ≥ 0.80                     | ✅           |
| nDCG@10   | 0.7983        | 0.8824         | ≥ 0.83                     | ✅           |

The clean branch beats the legacy oracle on every aggregate metric
(+0.078 recall, +0.075 MRR, +0.084 nDCG) and clears the locked embedded
thresholds. The oracle runs no threshold itself (Redis is not a target
runtime in `v0.11.0`); it is the comparison baseline.

## Intentional ranking differences (recorded, by design)

1. **Universal cosine gate (legacy bug).** Legacy applies `cosine >= 0.30`
   to *every* hit, including BM25-only hits; the clean branch gates vector
   candidates only and keeps lexical-only candidates valid. Fixture-proof:
   `tests/fixtures/legacy-baseline/behavior-v1.json`
   (`legacy-cosine-gate-drops-content-match`: with an engineered orthogonal
   vector the legacy stack returns `result_ids: []`,
   `content_match_returned: false`). The 12 corpus content-id cases do NOT
   trigger the legacy gate with real fp32 embeddings (their summaries pass
   ≥ 0.30 against the prompted RUNID queries), so the corpus alone would not
   have caught this bug — the deterministic fixture does.
2. **Document payload.** Legacy embeds summary-only; clean embeds
   `topic.replace("-", " ") + "\n" + summary.strip()` (input version 2).
   This is why clean Recall@5 (0.978) beats legacy (0.900) on identifiers
   and topic-bearing queries.
3. **Fusion site and window.** Legacy fuses inside Redis (top_k=20 window,
   BM25 branch scored inside the index); clean fuses at the app layer with
   locked candidate_limit=50 per branch and final top_k=5 (nDCG measured on
   fused top-10). Legacy returns up to 20 results; clean returns 5.
4. **Tie-breaks.** Legacy `recent` sorts by `period_start DESC` only (ties
   in index order, no `memory_id` tie-break); clean locks
   `created_at DESC, memory_id ASC`.
5. **Determinism of canonical scores.** Clean canonicalizes cosine to
   integer micro-cosine with half-even rounding and a locked floor; legacy
   compares raw floats.

## Behavior partition (24 cases)

- 12 content-only identifiers: legacy returns the expected doc in top-20
  (real embeddings pass the 0.30 gate); clean returns it via the lexical
  branch at rank 1 (candidate/fused-level assertion, see the GOAL-012 gate
  note — locked RRF/top-5 constants place it at fused rank 7–9 when the six
  live-tail docs collide on the shared `runid` token family).
- 6 punctuation-only: legacy degrades to KNN-only (all 20 hits
  `score_source=knn`); clean runs vector-only, never an error.
- 6 expired/deleted starvation: both stacks exclude the stale row and
  return the live tail.
