---
status: draft
created: 2026-08-04
last_updated: 2026-08-04
parent: .agents/plans/07-multiplatform-embedded-runtime.md
covers: GOAL-016
---

# Sub-plan 07.10 — Platform, footprint, and documentation gate (GOAL-016)

## Summary

Final release gate: CI matrix, doctor, harness connectors, resource evidence on
the checksummed reference machine, documentation refresh, and a full release
rehearsal from an empty user profile. Success criteria 1–11 in the master plan
are the exit checklist.

## Tasks

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-083 | CI wheel/build/install/E2E matrix: Windows x86_64, macOS 14+ ARM64, Ubuntu 22.04/24.04 x86_64, Python 3.12–3.14; forced NumPy fallback; wildcard/hostname/LAN HTTP-bind rejection on every OS family; IPv6 `::1` positive where supported. | | |
| TASK-084 | Linux ARM64 / Windows ARM64 best-effort wheel-resolution/fallback; report unsupported macOS Intel and musl explicitly instead of silent source builds. | | |
| TASK-085 | `another-brain doctor`: package/model hashes, tokenizer/profile, SQLite bootstrap/readonly invariants, schema/integrity/FTS/extension-or-fallback, isolated write/search/delete probe, paths, actionable per-item results. | | |
| TASK-086 | Update harness connectors to invoke installed `another-brain`; add Windows-capable examples; remove Docker/Redis/uvx assumptions. | | |
| TASK-087 | Measure clean/model disk (≤450 MiB), cold/warm latency (≤128-token warm p95 ≤100 ms), one-/two-process memory (≤500 MiB steady RSS), SQLite retrieval p95 at 10k/50k/100k (≤25/75/150 ms), startup; emit evidence manifest + raw samples; enforce budgets or record an approved revision. | | |
| TASK-088 | Update root README, `docs/architecture.md`, deployment/MCP/trust docs, skill guidance, `.agents/TESTING_GUIDE.md`, `.agents/PROJECT_CONTEXT.md` from real final commands and paths. | | |
| TASK-089 | Release rehearsal from an empty profile with only `uv`: install tool, configure one harness, first model install, remember/search/get/reinforce/forget, restart, doctor, uninstall; verify no daemon/container/server prerequisite. | | |
| TASK-090 | Set plan status `done` only after: clean tree/full CI, validated migration artifact, Q4/retrieval/concurrency evidence manifests, artifact hashes, resource report, docs gate. | | |

## Test Plan

- CI matrix green on all required platforms; fallback mode covered everywhere.
- Evidence manifests validate and match the checksummed reference machine for
  performance numbers.
- Rehearsal script is repeatable and recorded as release evidence.

## Assumptions

- Budgets change only through an approved plan revision backed by a failed-run
  manifest.
- `done` requires every prior sub-plan's gate, not just this one's tasks.
