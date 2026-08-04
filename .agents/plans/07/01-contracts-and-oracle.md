---
status: in-progress
created: 2026-08-04
last_updated: 2026-08-04
parent: .agents/plans/07-multiplatform-embedded-runtime.md
covers: GOAL-008
---

# Sub-plan 07.01 — Contracts and external `main` oracle (GOAL-008)

## Summary

Freeze the remaining GOAL-008 contract work before any new runtime code: record
the external `main` oracle, capture legacy baseline behavior as backend-neutral
JSON fixtures, define the JSONL v1 envelope, and write the final Protocols.
TASK-030 (architecture approval) is already done. No Redis code is created or
modified in `v0.11.0`.

Full contract details live in the master plan (JSONL v1 envelope, protocol
semantics, retrieval bug-fix fixtures). This sub-plan must not restate them in
a way that can drift; quote only what execution needs.

## Tasks

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-035 | Record `main` baseline `edc0e57` plus worktree commands as the external oracle in `.agents/PROJECT_CONTEXT.md` (or `.agents/decisions/`): exact commit, `git worktree add ../another-brain-main main` invocation, and the rule that Redis runtime code never re-enters `v0.11.0`. Verify `edc0e57` exists on `main` and is an ancestor of `main` before recording. | ✅ | 2026-08-04 |
| TASK-034 | Write the final backend-neutral Protocols as a pure-contract module (no Redis types, no score encodings, no backend selector): `MemoryRepository` (store/get/recent/reinforce/soft_delete/restore/hard_delete), `MemoryRetriever`, `AuditRepository` (record/list_day), `EmbeddingProvider` (embed_document/embed_query + health state). Encode the locked semantics in docstrings: collection ops use normalized `(brain_id, scope, scope_id)`; by-ID ops key on `(bound brain_id, memory_id)` with scope read from the stored row; cross-brain IDs return the `not_found` shape. | | |
| TASK-031 | In a separate worktree pinned to `main` baseline `edc0e57`: run and record the legacy unit/integration baseline; export deterministic fake-vector fixtures (identity binding, append-only writes, TTL, reinforce, soft-delete/restore, recent ordering `created_at DESC,memory_id ASC`, audit privacy, MCP previews, health) into backend-neutral JSON under `tests/fixtures/legacy-baseline/` in the `v0.11.0` branch. | | |
| TASK-032 | Add desired retrieval fixtures that lock the bug fix: a lexical-only `content` identifier survives with cosine below 0.30; vector-only candidates below 0.30 do not; deleted/expired rows are absent before branch limits. | | |
| TASK-033 | Turn the JSONL v1 envelope (manifest/data/trailer, canonical payload JSON, idempotency keys, ordering, checksums, absolute expiry) into a frozen contract doc plus hand-written valid/invalid fixtures. Invalid fixtures cover: missing manifest field, bad `payload_sha256`, non-contiguous `seq`, wrong sort order, NaN/Infinity, embedding bytes present. | | |

## Test Plan

- Protocol module imports with zero third-party dependencies; mypy/pyright or
  runtime `isinstance` structural checks pass on a minimal fake.
- Fixture JSON files parse against the locked shapes; invalid JSONL fixtures are
  rejected by a trivial reference validator script (not yet the real importer).

## Assumptions

- The legacy baseline run happens only in the external worktree; `v0.11.0`
  receives only the exported JSON fixtures.
- Protocols land inside the new `src/another_brain/` package location only
  after GOAL-009 creates the shell; until then they may live as a contract doc
  plus a small standalone module, to be moved by TASK-079.
