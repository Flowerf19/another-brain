# Sub-plan 07.11 — `setup` command + CLI polish → 0.12.0

Status: done
Parent: `../07-multiplatform-embedded-runtime.md`
Goal: ship 0.12.0 with one-shot onboarding and the CLI UX fixes that the
first real-user session (2026-08-07, maintainer's own machine) exposed.

## Background

0.11.0 shipped 2026-08-07 and was exercised immediately. The session
surfaced three real defects/UX gaps plus one gate false positive. None
weakens a frozen contract; all fold into 0.12.0 (unreleased — the tag is
created only after every task below is green).

## Tasks

| # | Task | Status | Date |
|---|---|---|---|
| TASK-094 | `another-brain setup`: one-shot onboarding composing `model pull` (idempotent) + `connect` for every detected harness + restart reminder. Code + 4 tests landed this session; README/CHANGELOG updated; version bumped to 0.12.0. | ✅ (unreleased) | 2026-08-07 |
| TASK-095 | clean-tree gate false positive: the gate's forbidden pattern includes `compose` (Docker Compose era); the setup docstring/CHANGELOG/test docs used "composes/composition" → gate FAIL. Reword to neutral verbs ("runs/bundles/steps"); never weaken the pattern — it guards against the old stack returning. | ✅ | 2026-08-07 |
| TASK-096 | Bare `another-brain model` / `another-brain admin` exit 2 silently (no output) because the sub-subcommand is optional. Make the subparsers `required=True` so argparse prints the usage error; pin with tests. Found by real usage: `another-brain model` printed nothing. | ✅ | 2026-08-07 |
| TASK-097 | Ctrl-C on `serve` leaks a `threading._shutdown` KeyboardInterrupt traceback. Reproduce (`timeout -s INT`), then make shutdown quiet and typed (conventional exit 130); fix at the level that actually silences it (catch in main vs daemon threads — decide from the repro, not a guess). **Landed `0b17b90`: root cause = anyio non-daemon stdin-reader blocked on the pipe, interpreter shutdown hung joining it; fix = SIGINT handler `os._exit(130)`, verified one Ctrl-C exits 130 in 0.26s, no traceback; integration + unit tests. Style/version follow-ups `0bcafbe`/`f3afdb8`.** | ✅ | 2026-08-07 |
| TASK-098 | Full command-matrix E2E against the built wheel: every subcommand incl. error paths (bare, --help/--version, setup, model pull/status, bare model, bare admin, doctor, recent, connect/--detect/unknown, import-jsonl bad path, serve + SIGINT). Then full suite + both gates green → commit → push main → tag v0.12.0 → publish workflow green. **Done: matrix all correct (bare model/admin usage, serve EOF 0, SIGINT 130 no traceback, doctor ok); 644 passed + both gates; publish run 31141890303 SUCCESS; PyPI 0.12.0 live.** | ✅ | 2026-08-07 |

## Notes

- `uv rm` / `uv remove` in the user session were CLI misconceptions
  (`uv tool uninstall another-brain` is the command); documentation already
  shows install only — no product change.
- The schema-ledger refusal that started the session was resolved by
  renaming the empty stale dev DB; a follow-up idea for a later release is
  a ledger line in doctor's real-DB check so doctor diagnoses what serve
  refuses. Recorded here so it is not lost; not in 0.12.0 scope.
