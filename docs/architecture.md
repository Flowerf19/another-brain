# Architecture

> **`v0.11.0` transition:** the approved target below is being implemented.
> The old top-level `src/` still contains the legacy Redis runtime until the
> early cleanup phase. Use `main` commit `edc0e57` as the legacy oracle; do not
> carry Redis/Docker forward in this branch.

Canonical sources:

- [approved target architecture](../.agents/plans/another-brain-architecture.md)
- [active implementation plan](../.agents/plans/07-multiplatform-embedded-runtime.md)
- [memory trust model](memory-trust-model.md)

Plans 01–05 are superseded Redis-era history.

## Target shape

```text
MCP host
  -> installed `another-brain` executable
  -> stdio default / localhost HTTP optional
  -> MemoryService
       -> Harrier 270M q4 via raw ONNX Runtime CPU
       -> SQLite memory/lifecycle/audit repository
       -> FTS5 lexical retrieval
       -> sqlite-vec scalar or NumPy exact vector retrieval
       -> app-layer RRF
```

Final runtime prerequisites are Python/uv and downloaded model artifacts — no
Docker daemon or Redis server.

## Target modules

```text
src/another_brain/
  cli.py, app.py, config.py
  domain/       diary models and retention
  embedding/    manifest, verified install, provider, payload, budgets
  storage/      SQLite connection, schema, memory, audit
  retrieval/    safe FTS query, lexical, vector, fusion, orchestration
  mcp/          tools and transports
```

Protocols isolate service tests but do not enable backend selection. SQLite is
the only runtime store.

## Memory and embedding

One append-only diary entry contains topic, catalog, summary, optional content,
timeline/identity fields, durable expiry/deletion, metadata, and one vector.
The vector is normalized FLOAT32[640] from:

```text
humanized topic
summary
```

Content is FTS5-only. The q4 ONNX graph already performs last-token pooling and
L2 normalization.

Tokenizer hard limits: topic 12, final document 256, prompted query 128,
content 1,024. No silent truncation or automatic chunking.

## Retrieval

FTS5 indexes topic/summary/content with initial BM25 weights 5:3:1. Vector
search is exact and applies cosine floor 0.30. Equal-weight RRF (`k=60`) fuses
branch ranks. Lexical-only candidates remain valid without cosine, fixing the
legacy content-only match bug.

Every branch filters brain/scope, durable expiry, and soft deletion before
limits. sqlite-vec failure chooses NumPy exact fallback rather than a source
build or install failure.

## Transition

Use a separate worktree for legacy evidence:

```bash
git worktree add ../another-brain-main main
```

The clean branch creates the final package shell, deletes Redis/Docker/Torch
early, then builds SQLite/retrieval/service vertically. Legacy data migration
uses neutral JSONL exported from a maintenance branch based on `main`; the clean
release imports and re-embeds it without a Redis dependency.
