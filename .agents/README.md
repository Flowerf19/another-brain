# Agent Docs

## Read order for `v0.11.0`

1. `.agents/AGENT_RULES.md` — branch rules and prohibited legacy shortcuts.
2. `.agents/PROJECT_CONTEXT.md` — concise transition and locked contracts.
3. `.agents/plans/another-brain-architecture.md` — approved target architecture.
4. `.agents/plans/07-multiplatform-embedded-runtime.md` — active implementation
   plan, execution order, gates, and append-only task IDs.
5. `.agents/TESTING_GUIDE.md` — phase-aware commands and acceptance layers.
6. `docs/memory-trust-model.md` — memories are claims, not facts.
7. `skills/another-brain/SKILL.md` — agent-facing recall/write/close-loop
   guidance.

The root README and public deployment/tool docs describe the clean target
runtime (rewritten in TASK-077, Docker/Redis-free); the README status block
tracks which phases have landed. Do not use legacy Docker/Redis commands as
target architecture.

## Plan lifecycle

- `another-brain-architecture.md` — approved source of truth.
- `07-multiplatform-embedded-runtime.md` — only active implementation plan,
  status `in-progress`.
- Plans 01–05 — superseded Redis-era history; useful only for legacy evidence.
- Plan 06 — completed usage-guidance history; still applicable where it does
  not conflict with the new topic/embedding contract.

GOAL and TASK IDs are append-only. Follow the explicit execution order in Plan
07 rather than numeric GOAL order: package shell, early legacy deletion, then
embedding/SQLite/retrieval/service.

## Branch boundary

`main` baseline `edc0e57` preserves the full Redis/Docker runtime. Use a
separate worktree for comparison:

```bash
git worktree add ../another-brain-main main
```

Branch `v0.11.0` must not retain or reintroduce Redis/Docker merely for parity.
If migration export is required, produce it from a maintenance branch based on
`main`; this branch imports neutral JSONL only.

## Critical target summary

```text
MCP stdio -> MemoryService
           -> Harrier q4 / raw ONNX Runtime CPU
           -> SQLite regular tables
           -> FTS5 + sqlite-vec scalar/NumPy exact
           -> app-layer RRF
```

One memory stores one vector from humanized topic + summary. Content is
lexical-only. Vector cosine floor applies only to vector candidates; lexical-
only results remain valid. Durable expiry/deletion filters apply before branch
limits. No Docker, Redis, Torch, SentenceTransformers, dual backend, ANN
sidecar, silent truncation, or auto-chunking belongs in the final runtime.

Do not invent implementation facts that have not landed. When commands, paths,
env vars, behavior, or support claims become real, update these docs and public
docs in the same change.
