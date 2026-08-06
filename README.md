# Another Brain

Shared long-term memory for MCP agents — one brain, many agents. A standalone,
fully embedded MCP tool: no server, container, or daemon required.

> **Status:** branch `v0.11.0` is an approved clean rebuild in progress.
> Landed: the flat `another_brain/` package at the repo root (schema v1,
> migrations, repository, durable TTL, lifecycle, audit), the Harrier q4
> ONNX embedding provider, hybrid retrieval (FTS5 BM25 + exact cosine +
> RRF), the eight `brain_*` MCP tools over stdio and opt-in loopback HTTP,
> and JSONL v1 import (GOAL-014). Pending: the release gate (GOAL-016) —
> `doctor` is still a typed not-yet-available stub.
> Authoritative design: `.agents/plans/another-brain-architecture.md`;
> execution record: `.agents/plans/07-multiplatform-embedded-runtime.md`;
> completed sub-plans are archived under `.agents/plans/archive/07/`.

## Target runtime

- ordinary SQLite tables + FTS5 (BM25 5:3:1) + exact cosine vectors
  (`sqlite-vec`, NumPy fallback) fused with RRF — one `brain.sqlite3` file in
  the per-user data directory;
- local Harrier OSS v1 270M q4 embeddings via raw ONNX Runtime CPU — no Torch,
  no network after the one-time pinned model install;
- MCP stdio by default, optional numeric-loopback HTTP;
- durable TTL diary: importance 5..1 → 365/180/90/30/7 days, soft delete with
  30-day grace, structural audit without memory text.

## Install

```bash
uv tool install another-brain
another-brain model pull   # one-time pinned + hash-verified model download
another-brain              # MCP stdio server (the default)
another-brain serve --http # optional loopback HTTP on 127.0.0.1:1905
```

Operational commands: `another-brain recent [--limit N]` prints the bound
brain's newest entries (no model required); `another-brain model status`
shows install state; `another-brain admin restore|hard-delete MEMORY_ID`
are the admin lifecycle operations; `another-brain import-jsonl PATH`
imports a JSONL v1 export artifact. `another-brain doctor` verifies
install/model/database and lands with the release gate (GOAL-016).

Harnesses invoke the installed `another-brain` executable.
Docker and Redis are not part of the install, runtime, or deployment model.
