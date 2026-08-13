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
`model pull` / `model status` / `recent` / `admin restore|hard-delete` /
`import-jsonl` are real commands and the MCP server (GOAL-013/014) serves
over stdio and opt-in loopback HTTP; only `doctor` (GOAL-016) still exits
with a typed not-yet-available error. The tree contains only the final
`another_brain/` package at the repo root (flat layout since 2026-08-06 —
imports unchanged, the hatch targets in `pyproject.toml` point at it) — no
Redis/Docker/Torch code, tests, config, or docs remain, and
`scripts/check-clean-tree.sh` keeps it that way.

- target: `.agents/plans/another-brain-architecture.md`;
- execution: `.agents/plans/07-multiplatform-embedded-runtime.md` (master) plus
  per-phase sub-plans under `.agents/plans/07/` (done phases 01–09 archived to `.agents/plans/archive/07/`);
- public README/deployment docs describe the clean target runtime (rewritten
  in TASK-077); the install contract is `python -m pip install another-brain`
  (uv remains an optional convenience, never a prerequisite).

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

The judged suite (harness retired; evidence consolidated in `benchmark.md` §2) ran on all four
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

**Scope removal 2026-08-05 (approved revision, TASK-057 note in 07.07).** The
mandatory `scope`/`scope_id` partition was removed product-wide; `brain_id` is
the only boundary and the retrieval suite is now whole-brain by construction.
The TASK-063/008 manifests and the 2026-08-04/05 retrieval-suite reports were
recorded against the removed scoped contract and are historical — their
recall/latency numbers are not directly comparable with post-removal runs.

**Closed 2026-08-05 — TASK-006 lexical benchmark.** `run_suite.py` measured
only `vector_branch` and `hybrid_search`; the weighted-FTS5 branch now ships
as a first-class `lexical_branch` series, measured once per store (BM25 has
no embedding dependency, so it is identical under both vector backends).
**It is the slowest branch at scale and nothing gates it**: p95 3.12 / 8.90 /
35.06 / 71.68 ms across the four stores, versus sqlite-vec 1.85 / 2.98 /
7.75 / 13.53 and NumPy 3.22 / 5.32 / 13.03 / 26.90. At 100k, BM25 is 5.3x
the vector branch and dominates the 116.92 ms hybrid p95, yet Success
criterion 9 budgets vector retrieval only. Left unbudgeted deliberately —
SC-9 is locked, so adding one is a plan revision; raised for TASK-087.
The cost is driven by **MATCH selectivity**, not store size: every extracted
term is OR-ed into the query, so the average judged query hits 53% of the
10k store and `"the"` alone matches 49.8%. A document-frequency filter via
`fts5vocab` (no hand-maintained stopword list, adapts to the corpus) buys
~18% at the safe end with recall unchanged — measured, deliberately not
implemented, and the full curve is recorded at TASK-087.
The wired-in series is proven at full protocol on the 10k store
(`retsuite-20260805T041805Z`, pooled n=5000); the other three sizes are
still hand measurements pending a full reference-machine sweep.

**Resolved 2026-08-05 — `NumpyVectorRetriever` is streaming by design.** It
was flagged as "not vectorized" against three descriptions that called it so;
the descriptions were wrong, not the code. Batching the BLOBs into one
`(N, 640)` matmul is 1.26x faster on the judged 100k store (17.19 vs
21.64 ms) but allocates 103x the peak (43.16 vs 0.42 MB), reaching 916.67 MB
when a single scope holds all 89223 live rows — past the 500 MiB budget.
Identical results either way. Wording corrected in `vector.py` and in
TASK-059; the trade is recorded at the class docstring so it is not
"optimized" back into a budget violation.

## Plan bookkeeping drift (observed 2026-08-04, resolved 2026-08-05)

Both stale rows are closed. **TASK-048** was blank only because the TASK-047
progress note said it waited on the migration runner — TASK-049 landed that
runner and the note was never cleared. Re-verified by executing the DDL:
column sets match the spec exactly, all seven required indexes exist,
`audit_events` is FK-free, and all 18 locked constraints reject as specified.
**TASK-006** is closed by the lexical benchmark above; 07.04 is `done` again.

The uncommitted `SearchPreview` / `domain/timeline.py` work is now assigned to
**TASK-064** (scoping note in 07.08). `timeline_day_for()` has no caller yet:
the repository persists `record.timeline_day` and both read paths filter on
it, so the missing link is the service deriving it at write time from
`AppConfig.timeline_timezone` — and the audit write path must use the same
helper so the two cannot disagree about which day a mutation belongs to.

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

- `brain_id`: process-bound isolation namespace — the only partition. The
  `scope`/`scope_id` partition was removed before release (approved revision
  recorded in `.agents/plans/07/07-retrieval.md`, TASK-057 note).
- `agent_id`: provenance detected from MCP `clientInfo`.
- Collection operations carry brain and live filters. Stable by-ID
  operations use `(process-bound brain_id, memory_id)`.

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
  `another_brain/services/embedding/model_manifest.py` (repo, revision, five file hashes, byte-exact
  query prompt + hash, document template, input version 2, dims 640, unit_l2);
  installer/provider/schema consume it. (The fp32/q4 spike and benchmark
  harnesses were retired to git history once the permanent gates moved into
  `tests/`; their evidence is consolidated in the root `benchmark.md`.)

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

Landed layout (flat, at the repo root):

```text
another_brain/
  cli.py config.py protocols.py errors.py
  domain/models.py          record types + locked validation
  domain/retention.py       importance -> durable expiry (no storage dep)
  domain/timeline.py        epoch ms -> YYYY-MM-DD diary day
  retrieval/               query, lexical, vector, fusion, service
  services/memory_service.py  use cases over the Protocols
  services/harness/        connect: harnesses.yaml data + registry/service
  mcp/tools.py              the eight brain_* tools (thin adapter)
  mcp/server.py             runtime assembly, stdio + loopback HTTP
  services/sql/             connection, migrations, schema, repository,
                            ttl, audit, health, profile, retry
  services/embedding/       model_manifest, model_installer, provider,
                            payloads, budgets
```

Startup splits eager storage from lazy model: a broken database fails at
launch, but the ONNX session loads on first embed because `brain_health` must
answer without it and a per-session stdio process must not pay seconds and
hundreds of MiB for sessions that never search. The tokenizer is the one
eager piece — budgets gate every embed, so an uninstalled profile is caught at
startup with the `model pull` message. `services/sql/profile.py` registers the
`embedding_profiles` row at open (nothing else did, and `memories.profile_id`
is a FK into it) and refuses rather than overwrites a stored profile that
disagrees with the manifest.

Console entry point:

```text
another-brain = another_brain.cli:main
```

The server surface uses Python MCP SDK `mcp>=2.0,<2.1` `MCPServer`; the legacy
pre-2.0 in-SDK `FastMCP` API is not part of the target package. Three v2
details worth knowing before touching `mcp/`: `MCPServer` is imported from
`mcp.server` (not the `mcp` top level); the handshake client name is
`ctx.session.client_params.client_info.name` — snake_case, with `clientInfo`
surviving only as a serialization alias, so the v1 spelling silently falls back
to the default agent id; and the same rename hits client-side result objects
(`server_info`, `is_error`), which matters when writing harnesses. **The SDK's
automatic loopback transport security is weaker than the locked policy**: it
allows the `localhost` name and any port (`127.0.0.1:*`). Explicit
`TransportSecuritySettings` pinned to the exact bound authority is therefore
required, never optional. A clean client
needs no Another Brain skill for correctness: initialize instructions,
self-contained tool/field descriptions, validation, and actionable errors carry
the contract — verified as a property (17 contract facts reachable from the
server surface alone). Per-argument text must use
`Annotated[..., Field(description=...)]`: the SDK builds each input schema from
the signature, so docstring prose reaches the tool description but never the
field. The skill is a thin adapter (TASK-091) holding only what a
schema cannot know — the claims-not-truth stance and the close-the-loop
timing.

Canonical install path (standard pip in a venv; uv optional):

```bash
python -m venv .venv
.venv/bin/python -m pip install another-brain
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
