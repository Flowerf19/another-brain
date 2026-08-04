# Test Report — Concurrency harness validation (TASK-007)

- **Date:** 2026-08-04 · **Verdict: PASS** (quick + full protocol, 61/61 checks)
- **Artifacts:** `benchmarks/concurrency/{harness,toy_adapter,run_harness}.py`;
  CI wrapper `tests/integration/test_concurrency_harness.py` (slow, `--quick`).

## What was validated

The reusable spawned-process driver and allowed-outcome oracle, against a
throwaway SQLite toy store that mirrors the locked production patterns (WAL,
busy_timeout, cross-process schema lock via filelock, `BEGIN IMMEDIATE` with
bounded busy retry + typed `BusyExhausted`, FTS5 external content with
triggers, expired/deleted live filtering). Deterministic fake payloads — no
ONNX, no real embeddings.

| Scenario | Parameters (full run) | Result |
|----------|-----------------------|--------|
| Fresh-open storm | 8 processes × 5 seeds, barrier-synced open of an absent DB | 8/8 opened, one schema version `[1]`, <1 s per storm |
| Mixed WAL workload | preseed 500, 4 procs × 500 ops (2 writers 40/25/20/15, 2 readers 60/25/15), 50 hot IDs, 5 seeds | zero unhandled errors, zero fatals, integrity ok, FTS trigger parity, no duplicate migrations |
| Crash probe | SIGKILL one writer at injected mid-run point, reopen, second mixed workload | crash fired once, integrity ok after crash, post-crash workload clean |
| Busy-exhaustion probe | external `BEGIN IMMEDIATE` lock held past the retry envelope (0.2 s timeout × 2 attempts) | typed `busy_exhausted` surfaced in 1.7 s, no hang, integrity ok |

## Bugs found and fixed during validation

1. **forkserver start method (Python 3.14 default):** module-level retry
   overrides in the parent did not propagate to children — retry envelope is
   now part of `WorkloadConfig` and travels through pickling.
2. **Join envelope:** a child still waiting out its sqlite busy timeout could
   outlive the parent's `join(timeout)`, so the temp tree was deleted under it.
   The driver now computes a `join_timeout_s` deadline and terminates
   survivors.

## Status vs plan

TASK-007 acceptance met: barriers, seeds, crash injection, and lock injection
validated on the toy. TASK-055 (GOAL-011) reuses this driver against the real
repository with both sqlite-vec and forced-NumPy modes, 500 ops/worker,
5 seeds, and the full post-run oracle (acknowledged-remember uniqueness,
allowed serializable race outcomes, restart checks).
