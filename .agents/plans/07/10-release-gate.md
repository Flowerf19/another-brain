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
> Raised from TASK-006 (2026-08-05): **SC-9 budgets the vector branch only,
> but the lexical branch is the slower one** — 71.68 ms p95 at 100k versus
> 13.53 ms for sqlite-vec, and it dominates the 116.92 ms hybrid p95. The
> `lexical_branch` series now ships in every retrieval-suite manifest, so the
> data is there; decide here whether BM25 earns a locked budget.
>
> **What actually drives that cost — corrected 2026-08-05.** Not the store
> size, and not (as first written here) the rows surviving the scope/live
> filter: it is **how many rows the MATCH itself hits**, which collapses
> because `build_match_query` ORs every extracted term including stopwords.
> Measured on the 10k store: a 63-term judged query matches 5857 rows (58.6%)
> and the average judged query matches 53.1%, because the single term `"the"`
> matches 4977 rows (49.8%) on its own. `bm25()` is then scored for every one
> of those before `LIMIT 50`. So the driver is term selectivity, and a
> threshold expressed against store size or candidates-per-scope would be
> measuring the wrong axis.
>
> A frequency filter is possible without any hand-maintained stopword map —
> `fts5vocab(main, memory_fts, row)` yields per-term document frequency
> straight from the index and adapts to the corpus. That matters here: the
> most frequent terms in the judged store include `kiem`, `tra`, `qua`,
> `ket`, `lai`, so an English stopword list would miss them entirely.
> Measured on 10k, dropping terms above a document-frequency threshold:
>
> | threshold | Recall@5 | lexical p50 | lexical p95 |
> |---|---|---|---|
> | none | 0.9700 | 4.92 ms | 8.78 ms |
> | < 0.3 | 0.9750 | 4.31 ms | 7.16 ms |
> | < 0.2 | 0.9700 | 3.72 ms | 6.89 ms |
> | < 0.1 | 0.9667 | 3.22 ms | 6.13 ms |
> | < 0.05 | 0.9583 | 2.00 ms | 4.80 ms |
>
> **Not implemented, deliberately.** The safe end of that curve buys ~18% and
> BM25 already down-weights common terms, so the ranking barely moves. Against
> that: it is a TASK-056 contract change with a behavior gate attached, it adds
> a vocab lookup per query whose cost is unmeasured, and the threshold is
> data-dependent — tuned on this synthetic mixed-language store it could drop
> meaningful terms in a real mostly-Vietnamese corpus. Worse, because the
> filter shifts as the index grows, the same query could return different
> results at different corpus sizes: the same size-dependent-behavior trap
> already rejected for the vector branch. Decide here, on real deployment
> data, not on this fixture.
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
