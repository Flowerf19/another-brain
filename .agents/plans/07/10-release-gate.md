---
status: draft
created: 2026-08-04
last_updated: 2026-08-06
parent: .agents/plans/07-multiplatform-embedded-runtime.md
covers: GOAL-016
---

# Sub-plan 07.10 — Platform, footprint, and documentation gate (GOAL-016)

## Summary

Final release gate. Restructured 2026-08-06 around the measured dependency
wheel matrix (evidence below) and the new per-OS `installer/` layout. Success
criteria 1–11 in the master plan are the exit checklist.

## Compatibility evidence (measured 2026-08-06, PyPI file matrices)

The platform story is bounded by the native dependencies, not by our code —
the package ships a pure `py3-none-any` wheel; all platform risk lives in
four wheels:

| dependency | pinned | wheel coverage | consequence |
|---|---|---|---|
| onnxruntime | 1.28.0 | linux x86_64/aarch64 (glibc≥2.27), **macOS 14+ arm64 only**, win_amd64, win_arm64 | **the hard constraint** |
| sqlite-vec | 0.1.9 | exactly 5 wheels: macOS x86_64, macOS arm64, manylinux x86_64/aarch64, win_amd64 — **no sdist, no musl, no win_arm64** | win_arm64/musl = **install-time resolution failure** (uninstallable), not a runtime fallback |
| tokenizers | 0.23.1 | universal incl. musl, win32, win_arm64 | never a blocker |
| numpy | 2.x | universal incl. musl, win32, win_arm64, macOS Intel | never a blocker |

Resulting support tiers:

- **Supported (CI-gated):** Ubuntu 22.04/24.04 x86_64, macOS 14+ Apple
  Silicon, Windows 10/11 x86_64 — Python 3.12–3.14.
- **Best-effort (wheels resolve, no CI hardware):** Linux aarch64 (full
  vector path — sqlite-vec ships a manylinux aarch64 wheel).
- **Currently uninstallable (install-time resolution failure — no wheel,
  no sdist):** Windows ARM64 and musl systems both fail at
  `uv tool install` on sqlite-vec. Unlocking win_arm64 = making sqlite-vec
  an optional/markered dependency; the runtime already degrades gracefully
  (`load_vec()` never raises → NumPy exact fallback per connection).
  musl stays unsupported regardless (onnxruntime has no musl wheel either).
- **Unsupported, reported explicitly (never a silent source build):**
  macOS Intel (onnxruntime ≥1.28 ships no x86_64 macOS wheel — uv
  resolution fails fast; release notes + doctor must say so), Alpine/musl,
  32-bit Windows, Linux ARMv7/ppc64le/s390x (onnxruntime absent).

Local code audit: no `os.symlink`, no `/proc` reads, no signal/fcntl usage
in product code; platformdirs resolves all user paths; `os.replace` for
atomic installs; `enable_load_extension`/`load_extension` guarded per
connection. No platform-specific product code exists today — every gap
above is dependency-driven.

## Phases

### Phase A — local, automatable (no GitHub needed)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-083 | `installer/` per-OS wheel gates: `linux/check-wheel-install.sh` (moved from scripts/, REPO_ROOT fixed two levels up), `macos/check-wheel-install.sh` (wrapper sharing the Linux source), `win/check-wheel-install.ps1` (PowerShell port; **untested until CI** — first real run is TASK-086). Clean-tree gate scans `installer/`. | ✅ | 2026-08-06 |
| TASK-084 | `another-brain doctor`: package/model hashes, tokenizer/profile, SQLite bootstrap/readonly invariants, schema/integrity/FTS/extension-or-fallback, isolated write/search/delete probe, resolved platformdirs paths, actionable per-item results. Includes an explicit OS-support verdict line (supported / best-effort / unsupported + reason, from the matrix above). Unblocks the TASK-075 rehearsal-gate reference. | | |
| TASK-085 | Forced-NumPy-fallback E2E switch: an env/CLI mechanism to disable sqlite-vec loading so CI and users can exercise the fallback path honestly (today only unit tests cover it via monkeypatch); wire into the wheel gates as a second pass. | | |
| TASK-091 | Unlock Windows ARM64 install: marker sqlite-vec out of win_arm64 (`sys_platform != 'win32' or platform_machine != 'ARM64'`), regenerate uv.lock, verify universal resolution + fallback import path. Runtime already degrades via `load_vec()`. (Decided 2026-08-06.) **Landed `e8f9c05`: marker + lock; aarch64-pc-windows-msvc resolves without sqlite-vec, x86_64 keeps 0.1.9; 489 unit passed.** | ✅ | 2026-08-06 |
| TASK-093 | `another-brain connect` — cross-platform harness setup in the Python CLI (approved 2026-08-06, replaces the sh-only path): detect harnesses via `$HOME/.<name>` dotdirs, register the MCP server as **stdio** `{"command": "another-brain"}` (kills the HTTP/`serve --http` assumption — zero-server), install the skill from a **wheel-bundled copy** (hatch force-include of `skills/another-brain/` — single source, no repo clone, no Node). sh connectors become wrappers or retire in TASK-088. | | |
| TASK-092 | Platform probe service (`another_brain/services/system.py`): single source of truth for OS/arch/libc detection + support-tier verdict (supported / best_effort / uninstallable / unsupported + reason + expect-sqlite-vec), pure/injectable for tests. Feeds the doctor verdict line (TASK-084); no CLI wiring here. (Decided 2026-08-06.) **Landed: `services/system.py` + 39 probe tests (528 unit passed).** | ✅ | 2026-08-06 |

### Phase B — CI matrix (needs GitHub runners)

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-086 | New `wheel-gate` workflow: matrix { ubuntu-22.04, ubuntu-24.04, macos-14, windows-2022 } × { Python 3.12, 3.13, 3.14 } running `installer/<os>/` gate + unit tests + clean-tree gate; plus one forced-fallback pass per OS (TASK-085). First execution of the Windows `.ps1` — budget iterations for pwsh semantics (native exit codes, path separators, `%LOCALAPPDATA%` resolution). | | |
| TASK-087 | ARM64 best-effort: attempt wheel resolution on linux-aarch64 / windows-arm64 runners if available to the repo at no cost; otherwise document as resolve-verified-only (PyPI matrix above) and move on. No paid runner time without maintainer sign-off. | | |

### Phase C — release

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-088 | After TASK-093 lands: turn `installer/linux/connect.sh` into a thin wrapper over `another-brain connect` (or retire it), refresh harness connection docs (Windows = same `another-brain connect`, zero manual JSON), remove Docker/Redis/uvx assumptions everywhere. | | |
| TASK-089 | Resource evidence for release notes: **accept `benchmark.md` as the evidence of record** (reference machine is checksummed; retrieval code untouched since those runs) — unless any retrieval/embedding diff lands before release, in which case restore the retired harness from git history and re-run. Release notes state: supported matrix + tiers, per-process ~322 MiB RSS model budget, NumPy-fallback p95 openly. Decide the parked lexical-branch budget question (see note below) as "no locked budget, documented behavior" unless new data appears. | | |
| TASK-090 | Release rehearsal from an empty profile with only `uv`: install tool, `model pull`, connect one harness, remember/search/get/reinforce/forget, restart, `doctor`, uninstall; verify no daemon/container/server prerequisite. Recorded as release evidence. Then: version `0.11.0` + CHANGELOG + tag; `uv publish` only on maintainer signal; set plan status `done`. | | |

## Open decisions (maintainer call before Phase B)

1. **Python floor:** matrix tests 3.12–3.14 but `requires-python = ">=3.11"`.
   Either add 3.11 to the matrix or bump the floor to 3.12. Recommendation:
   bump to 3.12 (one-line pyproject change; 3.11 adds a CI axis for zero
   known users).
2. ~~**ARM64 CI**~~ — **DECIDED 2026-08-06: no paid ARM runners.** Linux
   aarch64 stays best-effort (resolves, full vector path); recorded above.
3. **TASK-089 evidence:** confirm `benchmark.md` is sufficient as the
   release resource record (recommendation: yes).
4. ~~**Unlock Windows ARM64**~~ — **DECIDED 2026-08-06: yes** (TASK-091),
   plus a centralized platform probe service (TASK-092) so the support-tier
   logic lives in exactly one place.

## Parked from the old TASK-087 note (lexical-branch budget)

BM25 p95 at 100k is 71.68 ms vs 13.53 ms for the vector branch; driver is
MATCH-term selectivity, not store size (a 63-term query matches 58.6% of the
10k store; `"the"` alone 49.8%). Document-frequency filtering buys ~18% at
the safe end and is deliberately not implemented (contract change +
size-dependent behavior trap). Resolution folded into TASK-089: document,
don't budget.

## Windows install path evidence (verified 2026-08-06, for TASK-088 docs)

uv installer -> `%USERPROFILE%\.local\bin\uv.exe` (PATH auto, new shell;
`uv tool update-shell` if missing). `uv tool install another-brain`:
exe shim **copied** to `%USERPROFILE%\.local\bin\another-brain.exe`
(Windows copies, Unix symlinks); tool venv at `%APPDATA%\uv\tools\`
(Roaming — astral-sh/uv#7008 requesting Local is still open; harmless for
single-user machines). Product paths via platformdirs: DB at
`%LOCALAPPDATA%\another-brain\brain.sqlite3`, model at
`%LOCALAPPDATA%\another-brain\Cache\models\<profile>\` — Local, so the
206 MB model never roams. Same `BRAIN_DATA_DIR`/`BRAIN_MODEL_CACHE_DIR`
overrides as every OS.

## Test Plan

- `wheel-gate` workflow green on all four OS images × three Pythons,
  including a forced-fallback pass each.
- Windows `.ps1` gate proven on a real runner (TASK-086) — until then it is
  unverified code.
- Rehearsal script repeatable and recorded as release evidence.

## Assumptions

- onnxruntime's platform matrix is the binding constraint; if a future
  onnxruntime release restores macOS Intel or musl wheels, the unsupported
  tier can be revisited without product changes.
- No product code change is expected for platform support — every gap is
  dependency-wheel availability plus CI proof.
