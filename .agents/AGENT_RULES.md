# Agent Rules

## Source of truth

For the `v0.11.0` clean rebuild, read in this order:

1. `.agents/plans/another-brain-architecture.md` — approved target architecture.
2. `.agents/plans/07-multiplatform-embedded-runtime.md` — execution plan and gates.
3. `.agents/PROJECT_CONTEXT.md` — transition state and boundaries.
4. `.agents/TESTING_GUIDE.md` — phase-aware test commands.
5. `docs/memory-trust-model.md` — claims-not-facts contract before changing
   recall, injection, or ingest behavior.

Plans 01–05 are historical Redis-era contracts and are superseded for this
branch. Plan 06 remains the usage-guidance history. Keep root `README.md` and
`.agents/` docs aligned with the target when implementation changes.

## Branch transition

`main` at `edc0e57` is the legacy Redis/Docker behavior oracle. Compare with it
from a separate worktree:

```bash
git worktree add ../another-brain-main main
```

Do not import Redis code, add a `STORAGE_BACKEND` switch, or make the new
protocols implement Redis. Branch `v0.11.0` removes Redis, Docker, Torch, and
SentenceTransformers early, after the final package shell/domain tests are
working. A maintenance branch based on `main` may provide a one-time JSONL
export; the clean branch only imports JSONL.

## Final runtime boundary

The target runtime is:

```text
MCP stdio
  -> MemoryService
  -> ONNX Runtime CPU / Harrier q4
  -> SQLite repository + FTS5 + sqlite-vec scalar
  -> app-layer lexical/vector RRF
```

No Docker daemon, Redis server, Redis dependency, Torch, SentenceTransformers,
LanceDB, DuckDB, ANN sidecar, or hidden model daemon belongs in the clean
runtime. SQLite is the only storage backend; there is no backend environment
flag.

## Memory contract

- One memory is one append-only timeline entry.
- `brain_id` is server-bound storage isolation; `agent_id` is MCP provenance.
- Scope is `user | project | global`; global pins `scope_id=global`.
- `topic` is a stable reusable semantic subject, not a workflow label.
- Topic is lowercase-kebab; target 3–8 Harrier tokens after humanizing hyphens,
  hard maximum 12.
- `summary` is one or two self-contained sentences.
- One vector is generated from `humanized topic + newline + summary`.
- `content` is FTS5-only and never embedded.
- Catalog, metadata, scope, time, and importance are filters/provenance.
- Token limits use the pinned Harrier tokenizer: topic 12, document 256,
  prompted query 128, content 1,024. Reject over-limit input; never silently
  truncate or auto-chunk.
- Reads do not renew TTL. Reinforce is the only normal renewal.
- Search/recent previews never return content or embeddings; use `brain_get` for
  detail.

## Storage rules

- Use ordinary SQLite tables as source of truth.
- Use `sqlite-vec` scalar exact cosine on regular FLOAT32 BLOBs; use NumPy exact
  scan only as the compatibility fallback.
- Use FTS5 over topic, summary, and content with initial weights 5:3:1.
- Every query includes `brain_id`, scope, scope_id, `expires_at > now`, and
  `deleted_at IS NULL` before applying limits.
- Configure WAL, `foreign_keys=ON`, `synchronous=NORMAL`, 5-second busy timeout,
  short transactions, and bounded retries.
- Persist `expires_at`; correctness cannot depend on a cleanup sweep.
- Store model/revision/precision/dimension/input version in an embedding profile.
- Schema and model installation must be cross-process locked and crash-safe.
- Never expose secrets or memory text in audit/health output.

## Retrieval rules

- Lexical and vector retrieval are separate modules.
- Vector candidates use the cosine floor `0.30`.
- Lexical-only candidates do **not** need to pass cosine; this fixes the legacy
  content-only match bug.
- Fuse equal branch ranks with RRF `k=60`; use deterministic tie-breaking.
- A no-safe-term query uses vector retrieval only.
- FTS5 query construction must not expose MATCH syntax injection.
- Preserve exact names, ids, commands, paths, dates, and numbers in summaries.

## Embedding rules

- Runtime is raw `onnxruntime` CPU + `tokenizers`.
- Use pinned ordinary q4 artifact and matching `.onnx_data`, verified by hash.
- The graph already returns normalized `sentence_embedding[640]`; do not repeat
  pooling or normalization.
- Query prompt is used only for queries, never documents.
- Do not auto-select q4f16/int8 by hardware.
- A model/prompt/payload change requires input-version migration and re-embedding.
- Do not load the model merely to answer health/status.

## MCP rules

- Keep stable `brain_*` tool names.
- Bind `brain_id` from config and `agent_id` from the MCP handshake; never add
  either as a tool argument.
- Keep `brain_remember` guidance explicit about the topic+summary vector and
  topic token contract.
- Search/recent return previews; `brain_get` returns full content/metadata.
- Keep the trust loop: search, get when needed, reinforce only after use,
  forget when wrong.

## Coding workflow

- Implement the plan in phase order; do not broaden scope with speculative
  abstractions.
- Keep module boundaries explicit: domain, embedding, storage, retrieval, MCP.
- Run focused tests after each task and the full clean suite at each gate.
- Use `read` for source inspection, `rg` for references, and exact targeted
  edits for existing files.
- Do not modify runtime code while doing a documentation-only sync.
- When source paths, commands, env vars, or behavior changes, update
  `.agents/PROJECT_CONTEXT.md` and `.agents/TESTING_GUIDE.md` in the same change.
