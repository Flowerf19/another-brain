---
status: done
created: 2026-08-04
last_updated: 2026-08-05
parent: .agents/plans/07-multiplatform-embedded-runtime.md
covers: GOAL-011
---

# Sub-plan 07.06 — SQLite schema, repository, lifecycle, audit (GOAL-011)

## Summary

Implement storage end to end: connection factory with the three locked open
modes, checksummed schema v1 with FTS5 triggers, memory repository with durable
TTL and lifecycle transactions, and secret-free audit. The full schema DDL
contract, connection-mode behavior, and retry rules are in the master plan —
they are normative and not re-derivable here.

Module targets: `storage/connection.py`, `schema.py`, `repository.py`,
`audit.py`; domain: `domain/models.py`, `domain/retention.py`.

## Tasks

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-047 | `SQLiteConnectionFactory`: bootstrap (schema lock, autocommit, fresh-DB `page_size=16384` before first object, WAL, `synchronous=NORMAL`, fail fast on wrong page size in non-empty DB), normal read/write (local PRAGMAs, verify invariants, short `BEGIN IMMEDIATE`), read-only (`mode=ro`, `query_only=ON`, inspect never mutate). Narrow enable-load-disable window for sqlite-vec; per-connection NumPy fallback capability; guaranteed close in `finally`. | ✅ | 2026-08-04 |
> Progress 2026-08-04: models-first delivered early — `domain/models.py`
> defines MemoryRecord/AuditEvent/ImportRun/EmbeddingProfile/RecentFilters/
> SearchPreview with locked validation, and `schema.py` now carries the full
> v1 DDL (six tables, CHECK constraints incl. `CHECK(length(embedding)=2560)`,
> FTS5 external-content + triggers, seven indexes, `checksum()` for TASK-049).

| TASK-048 | Schema v1 exactly per master plan: `schema_migrations`, `embedding_profiles`, `memories` (all locked CHECKs incl. `UNIQUE(brain_id,memory_id)`, `CHECK(length(embedding)=2560)`), external-content `memory_fts(topic,summary,content)` with insert/update/delete triggers, `import_runs`, `audit_events` (no memory FK), and the required indexes/orderings. | ✅ | 2026-08-05 |
> Verified 2026-08-05. The row sat blank because the TASK-047 note above said
> it "remains open for the migration-runner integration" — but TASK-049
> landed that runner and the note was never cleared. Re-checked against the
> master-plan spec by executing the DDL, not by reading it: six tables (plus
> FTS5 shadows), three triggers, and all seven required indexes present —
> `UNIQUE(brain_id,memory_id)`, scoped recent/topic/catalog carrying deletion
> and expiry, `memories_expiry`, `memories_deleted`, and
> `audit_events(brain_id,timeline_day,event_at DESC,event_id ASC)`. Every
> table's column set matches exactly (no missing, no extra); `audit_events`
> has no foreign key, `memories` has exactly the profile FK, and the FTS5
> tokenizer is `unicode61 remove_diacritics 2`. All 18 locked constraints
> reject as specified — scope domain, canonical global `scope_id`, importance
> 1..5, non-empty identity/text, JSON-object metadata, 2560-byte BLOB,
> `updated_at>=created_at`, ordered period, positive `record_version`,
> profile FK, and the by-ID uniqueness.
| TASK-049 | Migration runner: checksum validation, `PRAGMA user_version`, exclusive schema transaction, concurrent-creator safety, crash rollback, fail-fast on unknown/newer versions. | ✅ | 2026-08-04 |
| TASK-050 | Append-only store/get/recent: normalized scope tuple; by-ID get on `(bound brain_id, memory_id)`; recent ordering `created_at DESC,memory_id ASC`; strict JSON metadata; row+FTS commit atomically. | ✅ | 2026-08-04 |
| TASK-051 | Durable TTL: persist `expires_at` from importance (5..1 → 365/180/90/30/7 days); every live read excludes expired/deleted before limits; bounded startup/opportunistic purge; never renew on read. | ✅ | 2026-08-04 |
| TASK-052 | Reinforce/soft-delete/restore/hard-delete transactionally by `(bound brain_id,memory_id)`; forget sets `deleted_at` and `expires_at=min(current, now+30d)` (never extends); reinforce/restore re-arm from importance; cross-brain IDs return `not_found`. | ✅ | 2026-08-04 |
| TASK-053 | SQLite audit: forbidden-text validation (never topic/summary/content/metadata), 90-day bounded best-effort cleanup isolated from committed memory mutations, day reads `event_at DESC,event_id ASC` keyed `(brain_id, day)`. | ✅ | 2026-08-04 |
| TASK-054 | Repository contracts: bootstrap/reopen/read-only, wrong page size, extension fallback, temp files, restart, malformed rows, injected clock/boundaries/rollback, retry classification (`SQLITE_BUSY`/`SQLITE_LOCKED` only, validation/integrity never retried), close/file-release assertions. | ✅ | 2026-08-04 |
| TASK-055 | Run the accepted concurrency workload (07.04 harness) against the real repository: timeouts, typed busy-exhausted error, allowed races, migration uniqueness, restart, `integrity_check`/`foreign_key_check`, FTS trigger parity on all rows + live filtering, resource closure. | ✅ | 2026-08-04 |
> Evidence 2026-08-04: `benchmarks/concurrency/run_repository.py` full locked
> parameters (5 storm seeds × 8 processes; 5 seeds × 2 vector modes mixed
> 2w/2r × 500 ops; crash probe; busy probe) — 200/200 oracle checks ok, zero
> unhandled errors/busy, typed `BusyExhausted` at ~26.5s in the probe,
> restart read/write green. CI gate: `--quick` via
> `tests/integration/test_concurrency_repository.py` (slow). Reader `search`
> uses raw FTS5 SQL pending the GOAL-012 lexical module. |

## Test Plan

- Unit: TTL math, retry classification, audit privacy
  rejection, row mapping, migration checksums.
- Integration: real temp SQLite with FTS5 (+sqlite-vec when available),
  restart/expiry/deletion/restore, crash injection, concurrent creators,
  read-only mode never mutates.
- Concurrency: full accepted workload with post-run integrity/FTS parity
  assertions, in both extension and forced-NumPy modes.

## Assumptions

- No model inference, tokenization, or network I/O inside any transaction.
- All timestamps are signed INTEGER epoch ms; `timeline_day` uses the
  configured timezone.
