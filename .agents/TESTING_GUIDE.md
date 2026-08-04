# Testing Guide

## Transition rule

Branch `v0.11.0` is the clean SQLite wheel; the legacy Redis/Docker runtime
was deleted in GOAL-015 and never returns:

- use a separate `main` worktree for legacy behavior evidence;
- never install/reintroduce Redis or Docker into `v0.11.0`;
- `scripts/check-clean-tree.sh` enforces both rules and runs in CI.

Legacy oracle setup:

```bash
git worktree add ../another-brain-main main
```

Baseline oracle commit is `edc0e57` unless Plan 07 records a later maintenance
export commit.

## Current commands

The clean tree requires no external service:

```bash
uv run pytest -m "not slow"     # fast suite (unit + integration)
uv run pytest                   # everything, incl. slow gates
uv run pytest tests/unit
scripts/check-clean-tree.sh     # dep graph + zero-reference gate
scripts/check-wheel-install.sh  # clean wheel install gate
uv build --no-sources
```

Evidence harnesses (run from the repo root, not CI):

```bash
uv run python benchmarks/concurrency/run_harness.py --quick   # toy concurrency validation (TASK-007)
uv run python benchmarks/run_benchmarks.py --help             # retrieval latency harness (full locked protocol runs at TASK-063)
uv run python benchmarks/measure_embedding_memory.py --profile-dir DIR  # per-process RSS/PSS (TASK-044)
```

The permanent q4 gate (`tests/integration/test_q4_embedding_gate.py`, slow)
skips when the pinned profile is not installed; install it with
`another-brain model pull` (or point `BRAIN_MODEL_CACHE_DIR` at a cache that
already holds the profile).

For trustworthy legacy evidence, run the old suite from
`../another-brain-main`, never from this branch. Do not treat a green legacy
suite as approval of the new retrieval contract; the universal cosine gate
has a known content-only match bug.

## Clean-branch target commands

The clean tree requires no external service:

```bash
uv run pytest                    # permanent unit + SQLite integration suite
uv run pytest tests/unit
uv run pytest tests/integration
uv build --no-sources
```

Wheel acceptance always installs into a clean environment and invokes the
console script from the wheel, not editable checkout imports.

## Required test layers

### Unit

- domain identity/scope/topic/catalog validation, including normalized scoped
  collections and `(bound brain_id,memory_id)` by-ID isolation;
- Harrier-token budgets at exact limit and limit+1;
- topic+summary payload and query prompt bytes;
- q4 manifest/hash/install failure paths and provider output validation;
- durable TTL/soft-delete/reinforce/restore math;
- SQLite bootstrap, normal read/write, and `mode=ro` mapping; migration
  checksum, busy retry, audit privacy;
- safe FTS5 query construction;
- lexical ranks, vector cosine floor, NumPy parity, deterministic RRF;
- service and MCP response contracts with fakes;
- initialize instructions and `tools/list` names, descriptions, and field
  schemas are sufficient without loading `skills/another-brain/SKILL.md`.

### SQLite integration

Use real temporary files, not mocks:

- schema bootstrap/reopen/concurrent create, readonly no-write behavior, wrong
  page-size failure, and unknown-version refusal;
- FTS5 triggers and weighted topic/summary/content retrieval;
- sqlite-vec scalar path and forced NumPy fallback on identical fixtures;
- expired/deleted filtering before limits;
- append/get/recent/reinforce/forget/restore/hard-delete;
- process restart, rollback/crash injection, integrity checks;
- the accepted spawned-process workload from Plan 07, including fresh-open,
  mixed readers/writers, injected crash, and bounded busy exhaustion;
- secret-free audit retention;
- resource close/file release.

### Retrieval regressions

Permanent fixtures must prove:

1. an exact identifier found only in `content` survives even when cosine is
   below 0.30;
2. vector-only candidates below 0.30 are excluded;
3. a candidate in both branches gains both RRF contributions;
4. Vietnamese with/without diacritics remains searchable;
5. punctuation-only/no-safe-term queries use vector-only retrieval;
6. expired/deleted rows cannot starve live candidates;
7. sqlite-vec and NumPy fallback return identical candidate IDs/order/RRF;
   raw cosine diagnostics differ by at most `1e-6` and canonical integer
   micro-cosine controls floor/ties.

### Embedding slow tests

With the pinned q4 artifacts:

- direct graph output is FLOAT32 `[batch,640]`, finite and unit normalized;
- documents are unprompted; queries use the exact pinned prompt;
- quality evidence uses checksummed `embedding-quality-v1` and enforces the
  Plan 07 cosine/Recall@5/MRR/nDCG@10 thresholds;
- measure cold/warm token buckets, steady/peak RSS, and two-process PSS;
- interrupted/concurrent model installation never exposes partial artifacts.

Torch/SentenceTransformers are evaluation-only and must not enter the clean
wheel or final lockfile.

### End-to-end

From an isolated user data/model directory and installed wheel, first with no
Another Brain skill installed:

```text
install -> bare stdio start -> remember -> search -> get -> reinforce ->
forget -> restart -> verify persistence/expiry -> doctor
```

After model installation, run an offline variant. Repeat once with the optional
thin skill and require identical tool correctness; only proactive recall timing
may differ. Redis and Docker must be absent, not merely stopped. Optional HTTP
tests must accept only numeric loopback binds and reject wildcard/hostname/LAN
addresses plus hostile Host/Origin headers before tool dispatch.

## Migration testing

The Redis exporter runs only from a maintenance branch/worktree based on
`main`. It emits versioned neutral JSONL. Clean-branch tests consume checked
fixtures or subprocess output and verify:

- IDs, identity, timestamps, metadata, absolute `expires_at_ms`, deletion, and
  secret-free audit;
- expired records are skipped;
- embeddings are omitted from export and recomputed as q4 input version 2;
- import validates canonical line/artifact hashes, is resumable/idempotent via
  durable batch checkpoints, and rejects same-key conflicts;
- interruption/retry reports imported/skipped/failed deterministically.

## Platform and release gates

Required CI matrix:

- Windows x86_64;
- macOS 14+ ARM64;
- Ubuntu 22.04/24.04 x86_64;
- Python 3.12–3.14;
- forced NumPy fallback in every required OS family.

Before release, run:

```bash
git diff --check
uv run pytest
uv build --no-sources
```

Then verify the Plan 07 resource budgets, clean dependency graph, zero runtime
Redis/Docker/Torch references, real wheel import path, and documentation/CLI
examples. A task is not complete merely because unit tests pass; use the
specific GOAL gate in Plan 07.
