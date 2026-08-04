---
status: frozen
created: 2026-08-04
last_updated: 2026-08-04
source: .agents/plans/07-multiplatform-embedded-runtime.md (GOAL-014)
---

# Contract — `another-brain-jsonl` format version 1 (TASK-033)

Neutral migration envelope between the legacy Redis runtime (external `main`
maintenance exporter) and the clean `v0.11.0` importer. Frozen: changes
require an approved plan revision, never an edit inside test code.

## Envelope

UTF-8, LF line endings (CRLF rejected), exactly one `manifest` line, then
ordered `memory`/`audit` data lines, then one `trailer` line. Every line is
canonical JSON: `json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=False, allow_nan=False)` — the raw line bytes must equal the
canonical serialization of the parsed object, and NaN/Infinity literals are
rejected.

### Manifest (line 1) — exactly these keys

| key | value |
|-----|-------|
| `kind` | `"manifest"` |
| `format` | `"another-brain-jsonl"` |
| `format_version` | `1` |
| `export_id` | UUID string |
| `source_app_version` | string |
| `source_schema_version` | string |
| `source_commit` | string |
| `source_embedding_profile` | string |
| `exported_at_ms` | integer epoch ms (captured once with writers quiesced) |
| `expiry_mode` | `"absolute_epoch_ms"` |
| `memory_count` | non-negative integer |
| `audit_count` | non-negative integer |

### Data lines — exactly these keys

`seq` (contiguous monotonic integers starting at 1), `kind`
(`"memory"`/`"audit"`), `idempotency_key`, `payload`, `payload_sha256`.

- `payload_sha256` = SHA-256 hex of the canonical payload JSON bytes (UTF-8).
- Memory lines come first, sorted by `(brain_id, memory_id)`;
  `idempotency_key = "memory:<brain_id>:<memory_id>"`.
- Audit lines follow, sorted by `(brain_id, event_at_ms, event_id)`;
  `idempotency_key = "audit:<brain_id>:<event_id>"`.

### Memory payload — exactly these 20 keys

`memory_id`, `brain_id`, `agent_id`, `scope`, `scope_id`, `topic`, `catalog`,
`summary`, `content`, `timeline_day` (`YYYY-MM-DD`), `period_start_ms`,
`period_end_ms` (both nullable), `created_at_ms`, `updated_at_ms`,
`importance` (1..5), `expires_at_ms` (absolute, authoritative),
`deleted_at_ms` (nullable), `metadata` (object), `record_version`,
`remaining_ttl_ms` = `max(0, expires_at_ms - exported_at_ms)`.

Embedding bytes and source embedding-profile IDs are absent; import
re-embeds topic+summary under input version 2. Scope tuples are normalized:
`scope_id` non-empty for `user`/`project`, pinned to `"global"` for `global`.

### Audit payload — exactly these 7 keys

`event_id`, `brain_id`, `memory_id`, `agent_id`, `action`, `event_at_ms`,
`detail` (object). `action` ∈ `remember | reinforce | forget | restore |
hard_delete`. Memory-text keys (`topic`, `summary`, `content`, `metadata`)
are forbidden anywhere in the payload. Audit may reference an expired memory
skipped by import — no cascading memory FK.

### Trailer (last line) — exactly these keys

`kind="trailer"`, `memory_count`, `audit_count` (final counts),
`last_seq` (= number of data lines), `rolling_sha256`.

`rolling_sha256` = SHA-256 hex over the concatenation of the exact bytes of
the manifest line and every data line, each terminated by one LF, in file
order (i.e. all file bytes before the trailer line). Cutover evidence also
records SHA-256 of the complete artifact.

## Import semantics (locked, implemented in GOAL-014)

- Importer captures `import_started_at_ms` once; records with
  `expires_at_ms <= import_started_at_ms` are skipped (audit facts remain
  importable). TTL is never rebased from import time; the relative verifier
  may differ by at most 1,000 ms for legacy source resolution.
- Embedding/tokenization happens outside transactions. Each batch atomically
  inserts records and advances `import_runs.last_committed_seq`.
- Same key + same preserved fields = `skipped`; same key + differing fields =
  conflict → roll back and abort the batch. A completed `export_id` with the
  same artifact hash is a whole-import no-op; same ID with another hash is
  rejected. Resume after any committed batch converges to identical final
  state and counters.

## Fixtures and validator

- Reference validator (stdlib only, not the real importer):
  `scripts/validate_jsonl_v1.py` — exit 0 with `OK`, exit 1 with the first
  contract violation on stderr.
- Fixtures: `tests/fixtures/jsonl-v1/valid-basic.jsonl` plus one file per
  violation class (`invalid-*.jsonl`). Coverage: missing manifest field, bad
  `payload_sha256`, non-contiguous `seq`, unsorted memory lines, non-finite
  number, embedding bytes present, bad rolling hash, CRLF line endings.
