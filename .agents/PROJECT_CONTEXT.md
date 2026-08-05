# Project Context

## Branch state

Branch `v0.11.0` is an approved clean rebuild in progress. Landed so far:
package shell (GOAL-009), early deletion (GOAL-015), evidence harnesses
(GOAL-001/002), the embedding subsystem (GOAL-005/010: manifest, installer,
provider, payloads, budgets), and the complete SQLite storage stack
(GOAL-011 TASK-047..055: connection factory, schema v1, migrations,
repository, TTL, lifecycle, audit, and the accepted concurrency workload
green on the real repository — 200/200 oracle checks, full locked
parameters), and retrieval (GOAL-012 TASK-056..063 plus the GOAL-002
oracle comparison TASK-031/008: safe FTS5 query builder, weighted BM25
lexical, exact cosine vector with sqlite-vec + NumPy fallback, pure RRF,
hybrid orchestrator, and the judged 1k/10k/50k/100k evidence suite).
`model pull` / `model status` are real commands; the MCP server
(GOAL-013), JSONL import (GOAL-014), and the release gate (GOAL-016) are
still pending — their CLI commands exit with
typed not-yet-available errors pointing at their GOAL. The tree contains only the
final `src/another_brain/` package — no Redis/Docker/Torch code, tests,
config, or docs remain, and `scripts/check-clean-tree.sh` keeps it that way.

- target: `.agents/plans/another-brain-architecture.md`;
- execution: `.agents/plans/07-multiplatform-embedded-runtime.md` (master) plus
  per-phase sub-plans under `.agents/plans/07/`;
- public README/deployment docs describe the clean target runtime (rewritten
  in TASK-077); the install contract is `uv tool install another-brain`.

## External legacy oracle (TASK-035)

The complete legacy Redis/Docker implementation is the external comparison
oracle, recorded and verified as:

- commit: `edc0e573a10bb8ea9148c9830cf19fe15f757972` (`edc0e57`), an ancestor
  of `main` (verified 2026-08-04); a later explicitly recorded
  maintenance-export commit may replace it;
- access: `git worktree add ../another-brain-main main` — run it from that
  worktree, or inspect with `git show main:<path>`;
- rule: Redis/Docker/Torch runtime code is never created, copied back, or
  checkpointed in `v0.11.0`; comparison happens only through the worktree,
  deterministic fixtures, or JSONL artifacts.

Do not preserve Redis in this branch merely for comparison.

## Retrieval evidence (TASK-063/008, accepted 2026-08-05)

The judged suite (`benchmarks/retrieval/run_suite.py`) ran on all four
stores and TASK-063/031/008 are ticked. Two results diverged from the letter
of the plan; both were traced to the fixture rather than to ranking code and
both are now approved revisions recorded in `.agents/plans/07/07-retrieval.md`:

- **`Recall@5 >= 0.90` applies to 1k/10k/50k only** (0.9800 / 0.9700 /
  0.9717); the 100k store (0.8567) is latency/parity evidence. Fillers share
  the judged scope `project/proj-1` and scale 1 → 288 → 1763 → 3583, they are
  written from natural-language templates so they earn real BM25 scores, and
  at 100k they take 67 of 600 top-5 slots — every one through the lexical
  branch. No query loses all its relevant docs at any size.
- **Parity gate = exact candidate IDs, exact ranks, exact fused RRF, raw
  scores within 1e-6**; exact `cosine_key` equality is gated on engineered
  unit fixtures. The suite's `exact_candidate_key_rank_match: False` on
  120/120 queries overstates it: IDs and ranks match exactly, fused RRF
  matches, and only the integer key differs by ±1 micro (max raw delta
  9.48e-07) because sqlite-vec accumulates in FLOAT32 while NumPy promotes to
  float64.

TASK-008: the clean branch beats the legacy oracle on every aggregate
(Recall@5 0.9783 vs 0.9000, MRR 0.9958 vs 0.9205, nDCG@10 0.8824 vs 0.7983).

**Open — TASK-006 lexical benchmark.** `run_suite.py` times `vector_branch`
and `hybrid_search` only; the weighted-FTS5 branch has never been
benchmarked despite TASK-006 requiring it. Hand-measured p95: 3.12 / 8.90 /
35.06 / 71.68 ms across the four stores, versus sqlite-vec 1.85 / 2.98 /
7.75 / 13.53 and NumPy 3.22 / 5.32 / 13.03 / 26.90. Lexical is the slowest
branch at scale and dominates the 116.92 ms hybrid p95 at 100k, yet Success
criterion 9 budgets only vector retrieval, so nothing gates it.

**Open — `NumpyVectorRetriever` is not vectorized.** The module docstring,
the class docstring, and TASK-059 all call it vectorized, but
`vector.py:157-165` loops row by row with a per-row `.astype()` allocation.
A batched matmul is the intended shape; the ~2x gap to sqlite-vec may be
mostly this rather than C-vs-Python. Fallback parity matters because
sqlite-vec is pre-1.0 and the release matrix includes Windows and ARM.

## Plan bookkeeping drift (observed 2026-08-04, partly resolved 2026-08-05)

Uncommitted work adds `catalog`, `timeline_day`, and `has_content` to
`SearchPreview`, plus `domain/timeline.py` and two new `RecentFilters`
fields. The stale unit test was fixed (suite green: 399 passed, 25 skipped).
The production change looks deliberate — `timeline_day` is persisted at write
time rather than recomputed from `created_at`, so a timezone change cannot
move a memory out of its filed day — but **no plan task claims it**; assign
it to a task or record a revision before GOAL-013 builds on it.

07.06 TASK-048 is still blank while the sub-plan reads `status: done`.
Schema v1 is in fact present and tested (six tables, FTS5 external content +
three triggers, six indexes; `test_schema.py` and `test_migrations.py` green,
35 passed) and TASK-049, the runner the row says it waits on, is ticked. The
row looks stale rather than genuinely open — confirm against the master
plan's required-index list before ticking.

## Product boundary

Another Brain is a standalone MCP memory service shared by trusted agent
systems. It owns timeline memory validation, identity binding, local embedding,
storage, retrieval, retention, audit, and MCP transport. It does not own client
agent loops, personas, Discord/project integrations, truth verification, or a
server-side summarization LLM.

Memories are claims, not facts. Code/current state wins; see
`docs/memory-trust-model.md`.

## Approved final architecture

```text
installed `another-brain` executable
  -> MCP stdio (default) / loopback HTTP (optional)
  -> MemoryService
  -> Harrier 270M q4 via raw ONNX Runtime CPU
  -> SQLite regular tables
       + FTS5(topic, summary, content)
       + sqlite-vec scalar exact cosine
       + NumPy exact fallback
       + app-layer RRF
```

The clean runtime has no Docker, Redis, Torch, SentenceTransformers, separate
vector database, ANN sidecar, or storage backend selector.

## Locked contracts

### Identity

- `brain_id`: process-bound isolation namespace.
- `agent_id`: provenance detected from MCP `clientInfo`.
- `scope`: `user | project | global`.
- `scope_id`: required for user/project; global pins `global`.
- Collection operations carry brain and normalized scope filters. Stable by-ID
  operations use `(process-bound brain_id, memory_id)` and read scope from the
  stored row; they never trust caller-provided scope.

### Diary and retention

- Append-only timeline entries; update = remember new + forget old.
- Fields include topic, catalog, summary, optional content, timeline timestamps,
  importance, durable `expires_at`, optional `deleted_at`, metadata, and one
  embedding.
- Importance TTL remains 365/180/90/30/7 days for levels 5..1.
- Reads are pure. Reinforce/restore re-arm TTL; forget sets
  `min(current_expires_at, now + 30 days)` and never extends life.

### Embedding

- One normalized FLOAT32[640] vector from
  `topic.replace("-", " ") + "\n" + summary.strip()`.
- Topic target 3–8 tokenizer tokens, hard max 12; use a stable reusable subject,
  not catalog/workflow/keyword stuffing.
- Document max 256 tokens including specials.
- Prompted query max 128 including specials.
- Lexical-only content max 1,024 without specials.
- Over-limit input is rejected; no truncation/chunking.
- q4 ONNX-community revision:
  `d59c919d0159aea2c19ed7d04288fcdd048d0f9c`.
- TASK-042 (2026-08-04): the q4 profile is encoded once in
  `src/another_brain/services/embedding/model_manifest.py` (repo, revision, five file hashes, byte-exact
  query prompt + hash, document template, input version 2, dims 640, unit_l2);
  installer/provider/schema consume it. The spike `fetch_models.py` imports its q4
  constants from the manifest so evidence and installer cannot drift.

### Retrieval

- FTS5 BM25 weights topic:summary:content = 5:3:1.
- Vector search is exact; cosine floor 0.30 applies only to vector candidates.
- Lexical-only candidates remain valid, fixing the legacy content-match bug.
- Equal-weight RRF uses `k=60`, fixed `candidate_limit=50` per branch, fixed
  final `top_k=5`, and deterministic ties. RRF `k` remains independent from the
  retrieval candidate count.
- Expired/deleted rows are filtered before each branch limit.

### Storage and concurrency

- One platform-user-data `brain.sqlite3` file.
- WAL, foreign keys, NORMAL sync, 5-second busy timeout, 16-KiB initial page
  size, short `BEGIN IMMEDIATE` writes, bounded busy retry.
- Schema/model download is cross-process locked and crash-safe.
- sqlite-vec failure selects NumPy fallback rather than source build/install
  failure.

## Target package

Landed layout (retrieval/ and mcp/ land with GOAL-012/013):

```text
src/another_brain/
  cli.py config.py protocols.py errors.py
  domain/models.py          record types + locked validation
  services/sql/             connection, migrations, schema, repository,
                            ttl, audit, retry
  services/embedding/       model_manifest, model_installer, provider,
                            payloads, budgets
```

Console entry point:

```text
another-brain = another_brain.cli:main
```

The server surface uses Python MCP SDK `mcp>=2.0,<2.1` `MCPServer`; the legacy
pre-2.0 in-SDK `FastMCP` API is not part of the target package. A clean client
needs no Another Brain skill for correctness: initialize instructions,
self-contained tool/field descriptions, validation, and actionable errors carry
the contract. The target skill is only a thin optional activation/project/trust
adapter; the checked-in longer legacy skill remains until TASK-091 lands
atomically with those descriptions.

Canonical install path:

```bash
uv tool install another-brain
another-brain
```

Harnesses invoke the installed command, not Docker, source checkout, or
unpinned `uvx`.

## Execution order

Because GOAL/TASK IDs are append-only, use the explicit phase order from Plan
07:

1. GOAL-008 — architecture and external-main fixtures (TASK-031 deferred to
   the TASK-008 oracle environment; approved revision 2026-08-04).
2. GOAL-009 — final package shell. ✅
3. GOAL-015 — early Redis/Docker/Torch deletion from `v0.11.0`. ✅
4. GOAL-001 and GOAL-002 TASK-005..007 — q4 evidence and reusable
   benchmark/concurrency harnesses. ✅
5. GOAL-005/010 — embedding. ✅
6. GOAL-011 — SQLite/lifecycle/audit/concurrency workload. ✅
7. GOAL-012 — BM25/vector/RRF. ✅ (TASK-056..063; two approved revisions on
   the TASK-063 evidence gate).
8. GOAL-002 TASK-008 ✅, then GOAL-013 — service/MCP.
9. GOAL-014 — neutral JSONL import/cutover.
10. GOAL-016 — platform/release/docs gate.

## Migration boundary

If legacy data needs export, implement/release the exporter on a maintenance
branch based on `main`. The clean branch receives only versioned JSONL and
re-embeds topic+summary under input version 2. No Redis dependency or exporter
source enters `v0.11.0`.

## Required release matrix

- Windows x86_64;
- macOS 14+ ARM64;
- Ubuntu 22.04/24.04 x86_64;
- Python 3.12–3.14.

Linux ARM64 and Windows ARM64 are best effort. macOS Intel and musl are not in
the initial support contract.
