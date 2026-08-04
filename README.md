# Another Brain

Shared long-term memory for MCP agents — one brain, many agents. A standalone,
fully embedded MCP tool: no server, container, or daemon required.

> **Status:** branch `v0.11.0` is an approved clean rebuild in progress. The
> checked-in package currently contains the final package shell (config, CLI,
> protocols); storage, retrieval, and the MCP server land in later phases.
> Authoritative design: `.agents/plans/another-brain-architecture.md`;
> execution record: `.agents/plans/07-multiplatform-embedded-runtime.md`.

## Target runtime

- ordinary SQLite tables + FTS5 (BM25 5:3:1) + exact cosine vectors
  (`sqlite-vec`, NumPy fallback) fused with RRF — one `brain.sqlite3` file in
  the per-user data directory;
- local Harrier OSS v1 270M q4 embeddings via raw ONNX Runtime CPU — no Torch,
  no network after the one-time pinned model install;
- MCP stdio by default, optional numeric-loopback HTTP;
- durable TTL diary: importance 5..1 → 365/180/90/30/7 days, soft delete with
  30-day grace, structural audit without memory text.

## Install (target contract)

```bash
uv tool install another-brain
another-brain            # MCP stdio server
another-brain model pull # one-time pinned model download
another-brain doctor     # verify install, model, and database
```

Harnesses invoke the installed `another-brain` executable.
Docker and Redis are not part of the install, runtime, or deployment model.
