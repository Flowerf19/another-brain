# Architecture

```text
MCP host
  -> installed another-brain executable
  -> stdio default / numeric-loopback HTTP optional
  -> MemoryService
       -> Harrier 270M q4 through ONNX Runtime CPU
       -> SQLite memory, lifecycle, and audit repository
       -> FTS5 lexical retrieval
       -> NumPy exact vector retrieval
       -> application-layer reciprocal-rank fusion
```

The runtime is a regular Python wheel. Native path selection is delegated to
`platformdirs`, so Windows and Ubuntu use their conventional per-user data and
cache locations without platform-specific branches in the storage layer.

## Package boundaries

```text
src/another_brain/
  cli.py, app.py, config.py
  domain/       memory records, validation, retention
  embedding/    pinned manifest, verified install, ONNX provider, payloads
  storage/      SQLite connection, schema, repository
  retrieval/    safe lexical search, exact vectors, RRF orchestration
  mcp/          MCP SDK v2 tool surface
```

SQLite is the only storage implementation. The database enables WAL, foreign
keys, NORMAL synchronous mode, a five-second busy timeout, and 16 KiB pages.
FTS5 indexes topic, summary, and content while the ordinary memory table
remains the source of truth.

One normalized FLOAT32[640] vector is generated from the humanized topic plus
summary. Content remains lexical-only. Vector candidates must meet cosine
0.30; lexical-only candidates remain eligible. The two ranked branches are
fused with equal-weight RRF (`k=60`) and deterministic tie-breaking.

Expiry is persisted on every row and enforced in reads. Reads do not renew a
memory; only reinforcement does. Forget is a recoverable soft deletion during
the configured grace period, while hard deletion is an explicit admin action.
