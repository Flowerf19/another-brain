# Test Report — SQLite vector / FTS5 benchmark (TASK-005/006)

- **Date:** 2026-08-04 · **Status: diagnostic evidence** (reduced protocol —
  full 5×1000-run deferred to the TASK-063 retrieval gate; see "Incident").
- **Runner:** `benchmarks/run_benchmarks.py` (parameterized: `--warmups
  --measured --runs --sizes --sleep-ms`)
- **Stores:** `benchmarks/generate_stores.py`, seed 20260804+size,
  `brain-bench-{1k,10k,50k,100k}.sqlite3` (3.8 / 36.7 / 177.7 / 351.2 MB),
  each embedding the 624 corpus docs + seeded fillers; scope 50/40/10
  user/project/global, importance-weighted TTL, 5% expired + 5% soft-deleted.
- **Filter under test:** brain + live (`expires_at_ms > now`, `deleted_at IS
  NULL`) — worst-case scan, no scope narrowing.

## Results (p95, reference machine)

| Store | vec (sqlite-vec) | NumPy fallback | FTS5 BM25 5:3:1 | Budget (vec) |
|-------|------------------|----------------|-----------------|--------------|
| 10k (probe) | 6.4 ms | 26.5 ms | — | ≤ 25 ms ✅ |
| 10k (partial protocol run) | **8.2 ms** | **44.6 ms** | **16.5 ms** | ≤ 25 ms ✅ |
| 100k (probe) | 30.3 ms | 231.3 ms | — | ≤ 150 ms ✅ |

Pooled p50/p95/p99 per run land in `benchmarks/evidence/` when the full
protocol runs. Budgets: 10k ≤ 25 ms, 50k ≤ 75 ms, 100k ≤ 150 ms (Plan 07
success criterion 9).

## Verdicts

1. **sqlite-vec (primary path) meets budgets** at every measured size with
   wide margin (8.2 ms vs 25 ms at 10k; 30.3 ms vs 150 ms at 100k).
2. **NumPy fallback exceeds budgets at scale** (44.6 ms at 10k, 231 ms at
   100k). The fallback is the compatibility path when the extension cannot
   load — acceptable per plan (extension failure selects fallback, never a
   build), but the release note should state fallback p95 openly (TASK-087).
3. **FTS5 slower than vector scan at 10k** (16.5 vs 8.2 ms): FTS5 evaluates
   `bm25()` for every match with no early termination; benchmark OR-queries
   carry up to ~40 terms (65–128-token bucket) and the synthetic store has
   heavy template-term repetition → huge posting lists. Production queries
   are typically ≤16 tokens. **Action for TASK-056/057:** consider capping
   the number of terms in safe FTS query construction.

## Parity contract

The canonical micro-cosine parity check (vec vs NumPy: exact candidate
IDs/order, |Δscore| ≤ 1e-6, half-even `round(score*1e6)`, floor 300000) is
implemented in the runner but **not yet executed** — deferred with the full
protocol run to TASK-063.

## Incident: thermal cutoff

The first full-protocol attempt (5 runs × 1000 measured × 3 modes × 4 sizes;
NumPy at ~200 ms/query at 100k ≈ 16 min sustained load) was manually aborted
when the machine reached **90 °C**. Decision (maintainer): existing data is
sufficient for this phase; the full locked protocol runs once, later, at the
TASK-063 retrieval gate under controlled conditions (the runner gained
`--sleep-ms` thermal pacing for that run).
