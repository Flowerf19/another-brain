# Architecture

> The authoritative architecture for the `v0.11.0` embedded rebuild is
> `.agents/plans/another-brain-architecture.md`, executed through
> `.agents/plans/07-multiplatform-embedded-runtime.md` and its sub-plans under
> `.agents/plans/07/`. This page is refreshed from the final implementation in
> TASK-088.

Summary: Another Brain is a standalone MCP tool. Eight `brain_*` tools sit in
front of a `MemoryService` over ordinary SQLite tables; retrieval is FTS5
BM25 (weights 5:3:1 over topic/summary/content) fused with exact cosine
vectors (`sqlite-vec` scalar or NumPy fallback) via equal-weight RRF `k=60`.
Embeddings are Harrier OSS v1 270M q4 through raw ONNX Runtime CPU over a
pinned, hash-locked model artifact. One `brain.sqlite3` file per user, shared
by independent stdio processes through WAL.

Embeddings run in-process: one lazy ONNX session per MCP process (first load
serialized under a lock, references closed on shutdown). There is no hidden
embedding daemon. Measured per-process memory is recorded in
`benchmark.md` §6 (~322 MiB RSS for the
session on the reference machine).
