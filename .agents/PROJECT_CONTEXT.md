# Project Context

## Branch state

Branch `v0.11.0` is an approved clean rebuild in progress. The checked-in
runtime still contains the legacy Redis/Docker implementation until the early
cleanup phase, so distinguish code evidence from target architecture:

- target: `.agents/plans/another-brain-architecture.md`;
- execution: `.agents/plans/07-multiplatform-embedded-runtime.md`;
- legacy oracle: `main` baseline `edc0e57`, preferably in a separate worktree;
- public README/deployment commands: legacy until the installed wheel exists.

Do not preserve Redis in this branch merely for comparison. Main can be
inspected with `git show main:<path>` or executed from another worktree.

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
  -> MCP stdio (default) / localhost HTTP (optional)
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
- Every storage/retrieval query carries brain and scope filters.

### Diary and retention

- Append-only timeline entries; update = remember new + forget old.
- Fields include topic, catalog, summary, optional content, timeline timestamps,
  importance, durable `expires_at`, optional `deleted_at`, metadata, and one
  embedding.
- Importance TTL remains 365/180/90/30/7 days for levels 5..1.
- Reads are pure. Reinforce/restore re-arm TTL; forget applies grace without
  extending a shorter remaining lifetime.

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

### Retrieval

- FTS5 BM25 weights topic:summary:content = 5:3:1.
- Vector search is exact; cosine floor 0.30 applies only to vector candidates.
- Lexical-only candidates remain valid, fixing the legacy content-match bug.
- Equal-weight RRF uses `k=60` and deterministic ties.
- Expired/deleted rows are filtered before each branch limit.

### Storage and concurrency

- One platform-user-data `brain.sqlite3` file.
- WAL, foreign keys, NORMAL sync, 5-second busy timeout, 16-KiB initial page
  size, short `BEGIN IMMEDIATE` writes, bounded busy retry.
- Schema/model download is cross-process locked and crash-safe.
- sqlite-vec failure selects NumPy fallback rather than source build/install
  failure.

## Target package

```text
src/another_brain/
  cli.py app.py config.py
  domain/
  embedding/
  storage/
  retrieval/
  mcp/
```

Console entry point:

```text
another-brain = another_brain.cli:main
```

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

1. GOAL-008 — architecture and external-main fixtures.
2. GOAL-009 — final package shell.
3. GOAL-015 — early Redis/Docker/Torch deletion from `v0.11.0`.
4. GOAL-001/002 — evidence gates.
5. GOAL-005/010 — embedding.
6. GOAL-011 — SQLite/lifecycle/audit.
7. GOAL-012 — BM25/vector/RRF.
8. GOAL-013 — service/MCP.
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
