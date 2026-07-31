# Testing Guide

## Transition rule

Branch `v0.11.0` is moving from the legacy Redis/Docker runtime to a clean
SQLite wheel. Test commands are phase-aware:

- use the current branch only for the code that still exists in that phase;
- use a separate `main` worktree for Redis behavior evidence;
- never install/reintroduce Redis or Docker into `v0.11.0` after GOAL-015.

Legacy oracle setup:

```bash
git worktree add ../another-brain-main main
```

Baseline oracle commit is `edc0e57` unless Plan 07 records a later maintenance
export commit.

## Current pre-cleanup commands

Until the package shell and early deletion land, the current checkout still has
the legacy test layout:

```bash
uv run pytest tests/unit
uv run pytest
```

The full legacy integration suite requires Redis and can silently skip. For
trustworthy Redis evidence, run it from `../another-brain-main`, not by keeping
Redis as a target dependency here. Record skipped tests with:

```bash
uv run pytest -rs
```

Do not treat a green legacy suite as approval of the new retrieval contract;
the universal cosine gate has a known content-only match bug.

## Clean-branch target commands

After GOAL-009/015, tests must require no external service:

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

- domain identity/scope/topic/catalog validation;
- Harrier-token budgets at exact limit and limit+1;
- topic+summary payload and query prompt bytes;
- q4 manifest/hash/install failure paths and provider output validation;
- durable TTL/soft-delete/reinforce/restore math;
- SQLite row mapping, migration checksum, busy retry, audit privacy;
- safe FTS5 query construction;
- lexical ranks, vector cosine floor, NumPy parity, deterministic RRF;
- service and MCP response contracts with fakes.

### SQLite integration

Use real temporary files, not mocks:

- schema create/reopen/concurrent create and unknown-version refusal;
- FTS5 triggers and weighted topic/summary/content retrieval;
- sqlite-vec scalar path and forced NumPy fallback on identical fixtures;
- expired/deleted filtering before limits;
- append/get/recent/reinforce/forget/restore/hard-delete;
- process restart, rollback/crash injection, integrity checks;
- two or more independent writer/reader processes and busy exhaustion;
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
7. sqlite-vec and NumPy fallback return the same ordered IDs.

### Embedding slow tests

With the pinned q4 artifacts:

- direct graph output is FLOAT32 `[batch,640]`, finite and unit normalized;
- documents are unprompted; queries use the exact pinned prompt;
- quality evidence includes cosine against fp32 reference plus Recall@5, MRR,
  and nDCG@10;
- measure cold/warm token buckets, steady/peak RSS, and two-process PSS;
- interrupted/concurrent model installation never exposes partial artifacts.

Torch/SentenceTransformers are evaluation-only and must not enter the clean
wheel or final lockfile.

### End-to-end

From an isolated user data/model directory and installed wheel:

```text
install -> bare stdio start -> remember -> search -> get -> reinforce ->
forget -> restart -> verify persistence/expiry -> doctor
```

After model installation, run an offline variant. Redis and Docker must be
absent, not merely stopped.

## Migration testing

The Redis exporter runs only from a maintenance branch/worktree based on
`main`. It emits versioned neutral JSONL. Clean-branch tests consume checked
fixtures or subprocess output and verify:

- IDs, identity, timestamps, metadata, remaining TTL, deletion, and audit;
- expired records are skipped;
- embeddings are omitted from export and recomputed as q4 input version 2;
- import is resumable and idempotent;
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
