---
status: draft
created: 2026-08-04
last_updated: 2026-08-04
parent: .agents/plans/07-multiplatform-embedded-runtime.md
covers: GOAL-009
---

# Sub-plan 07.02 — Installable final package and Redis-free config (GOAL-009)

## Summary

Establish the final `src/another_brain/` src-layout package with a Hatchling
build, locked dependency ranges, Redis-free config, platformdirs paths, the CLI
surface, and a clean-wheel install gate. Domain/tool response contracts are
preserved; persistence/retrieval are not implemented yet — the package shell
stays green with package/domain tests while feature-incomplete.

## Tasks

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-036 | Move runtime under `src/another_brain/` with explicit package imports; configure `hatchling>=1.31,<2` src-layout build and `[project.scripts] another-brain = "another_brain.cli:main"`. | ✅ | 2026-08-04 |
| TASK-037 | Lock core ranges: `mcp>=2.0,<2.1`, `onnxruntime>=1.28,<1.29`, `tokenizers>=0.23,<0.24`, `numpy>=2.1,<3`, `platformdirs>=4.3,<5`, `sqlite-vec>=0.1.9,<0.2`, `filelock>=3.16,<4`. Resolve exact versions in root `uv.lock`; remove Redis and root Torch extras. | ✅ | 2026-08-04 |
| TASK-038 | Implement Redis-free config: fixed retrieval/token contracts, `BRAIN_ID`, timezone/retention, data/model overrides, HTTP precedence/defaults. Accept numeric loopback IP literals only (`127.0.0.0/8`, `::1`); reject hostnames (including `localhost`), wildcard, LAN/public/link-local binds, invalid ports, and port zero outside test harnesses. | ✅ | 2026-08-04 |
| TASK-039 | Resolve default paths with `platformdirs`: `brain.sqlite3` in per-user data dir, immutable model artifacts in per-user cache dir; create dirs with user-only permissions where supported. | ✅ | 2026-08-04 |
| TASK-040 | Implement CLI: bare command = protocol-clean stdio (stdout reserved for MCP frames); `serve --http [--host HOST] [--port PORT]`, `model pull/status`, `doctor`, `recent`, `admin restore|hard-delete`, `import-jsonl`. Logs/progress on stderr; never import Redis/Torch/SentenceTransformers at startup. Subcommands that need unimplemented subsystems may exit with a clear typed "not yet available" error during the shell phase. | ✅ | 2026-08-04 |
| TASK-041 | Build sdist/wheel with `uv build --no-sources`, install the wheel into a clean environment, run `another-brain --help`, and fail if imports resolve from the checkout instead of the installed wheel. | | |

## Test Plan

- Unit: config validation matrix (loopback accepted; hostname/wildcard/LAN/
  link-local/invalid-port rejected), path resolution, CLI parsing.
- Integration: wheel build + clean-venv install + `--help`; import provenance
  check (`another_brain.__file__` outside the checkout).
- Gate: `uv build` output inspected — no `src/` flat-layout modules leak in.

## Assumptions

- `spikes/fp32/` is a standalone non-workspace project and never enters the
  root lock or wheel.
- MCP SDK v2 surface (`MCPServer`) is verified against the resolved version
  during this phase; any API drift is recorded before GOAL-013.
