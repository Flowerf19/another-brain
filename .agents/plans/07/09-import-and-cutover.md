---
status: draft
created: 2026-08-04
last_updated: 2026-08-04
parent: .agents/plans/07-multiplatform-embedded-runtime.md
covers: GOAL-014
---

# Sub-plan 07.09 — JSONL import and final cutover (GOAL-014)

## Summary

Consume the neutral JSONL v1 artifact produced by the external `main`
maintenance exporter, implement the resumable/idempotent importer with
`import_runs` checkpoints, and execute the gated cutover. The JSONL v1 envelope
and cutover sequence are locked in the master plan; `v0.11.0` never contains
Redis exporter code.

## Tasks

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-070 | In the pinned `main` maintenance worktree only: implement/release the JSONL v1 streaming exporter — quiesce writers, temp-write, self-validate counts/checksums, atomic publish, record commit/version/invocation/artifact hash. Clean `v0.11.0` consumes only the artifact. | | |
| TASK-071 | Implement clean `import-jsonl`: canonical envelope/hash/profile validation, absolute-expiry authoritative (never rebase TTL; relative verifier tolerance ≤1000 ms), audit preservation without memory FK, q4 topic+summary re-embedding outside transactions, skip records already expired at `import_started_at_ms`. | | |
| TASK-072 | `import_runs` batch checkpoints: each batch atomically inserts + advances `last_committed_seq`; same key/same fields = skipped, same key/differing fields = conflict rolls back and aborts; completed `export_id` + same artifact hash = whole-import no-op, different hash = rejected; resume converges to identical state/counters with an imported/skipped/failed report. | | |
| TASK-073 | Import fixtures produced by the external exporter; compare every non-embedding field, lifecycle result, lexical result, and expected re-embedded vector profile. | | |
| TASK-074 | Complete CLI/app/MCP/health/permanent tests on SQLite only; verify no backend selection or legacy runtime path re-entered the clean branch. | | |
| TASK-075 | Cutover gate: validated external artifact, clean wheel, full permanent/import/judged-retrieval suites, accepted concurrency workload, restart E2E, doctor, isolated-profile comparison — all green without Redis or Docker installed. Import into an isolated fresh profile first; switch harnesses only after parity passes; keep legacy data read-only as rollback backup. | | |

## Test Plan

- Unit: envelope/canonical-JSON/hash validation against 07.01 fixtures,
  expiry-skip math, conflict/skip/no-op classification.
- Integration: resume interrupted at every batch boundary → identical final
  state and counters; expired-skip preserves audit facts.
- E2E: full cutover rehearsal from a real exported artifact into an isolated
  profile, doctor + retrieval comparison, harness switch, rollback check.

## Assumptions

- A validated final export artifact must exist before cutover begins.
- After the first new SQLite write, returning to legacy requires an explicit
  reverse-migration decision and is not assumed lossless.
