---
status: done
created: 2026-08-04
last_updated: 2026-08-04
parent: .agents/plans/07-multiplatform-embedded-runtime.md
covers: GOAL-015
---

# Sub-plan 07.03 — Early clean-slate deletion (GOAL-015)

## Summary

Delete Redis, Docker, and Torch/SentenceTransformers from `v0.11.0` immediately
after the package shell is green — before any new storage/retrieval code. The
legacy implementation remains reachable only through the external `main`
worktree (sub-plan 07.01). Temporary feature incompleteness inside the branch
is acceptable; package/domain tests must stay green.

## Tasks

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-076 | Delete Redis repositories/index/keys, Redis audit implementation, Redis config/env parsing, backend flags, Redis-only fixtures/tests, and all imports; retain no Redis package extra. | ✅ | 2026-08-04 |
| TASK-077 | Delete `docker/`, `.dockerignore`, Compose/Docker install and health paths, Docker-specific model/cache assumptions, and Docker instructions from scripts/product docs. | ✅ | 2026-08-04 |
| TASK-078 | Delete runtime SentenceTransformers/Torch providers, precision code, PyTorch source config, root extras/tests/lock packages; fp32 survives only in the non-workspace `spikes/fp32/` frozen evaluation project. | ✅ | 2026-08-04 |
| TASK-079 | Move backend-neutral domain/tool response code into `src/another_brain/`, then delete superseded top-level `src/` modules/stubs and `pythonpath=["src"]` assumptions before new persistence/retrieval work begins. | ✅ | 2026-08-04 |
| TASK-080 | Regenerate root `uv.lock`; inspect wheel plus full transitive dependency graph; fail if Redis, Torch, SentenceTransformers, CUDA, LanceDB, DuckDB, or Docker tooling appears in root/core runtime. Check the isolated fp32 lock separately. | ✅ | 2026-08-04 |
| TASK-081 | Zero-reference check over `src/`, permanent `tests/`, scripts, product docs, README, pyproject, workflows for Redis/Docker/Torch runtime paths; only external-oracle instructions and clearly superseded historical plans may mention them. | ✅ | 2026-08-04 |
| TASK-082 | Mark archive plans 03/04/05 and conflicting rules superseded; update AGENT_RULES/PROJECT_CONTEXT so future agents cannot reintroduce Redis/Docker or summary-only embedding. | ✅ | 2026-08-04 |

## Test Plan

- `pytest` green on the remaining package/domain tests.
- Scripted grep gate (checked into scripts or CI) implementing TASK-080/081 so
  later phases cannot regress it.
- Wheel rebuild passes the TASK-041 clean-install gate after deletion.

## Assumptions

- Deletion is one reviewable commit series, not interleaved with new
  implementation.
- The Redis JSONL exporter is never built here; it belongs to a `main`
  maintenance worktree (sub-plan 07.09).
