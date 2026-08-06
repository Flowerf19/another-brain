# Per-OS install gates

Wheel install verification, split by operating system. Each gate builds the
wheel into a temp workspace, installs it into a fresh venv, proves the package
imports **from the venv** (checked from a neutral working directory so the
checkout's root `another_brain/` cannot shadow it), and verifies the typed
CLI contract on a machine with no model installed:

- bare `another-brain` exits 3 with a typed missing-model error on stderr
  (`model profile not installed; run ...model pull`) — never a traceback;
- the run is confined to the gate's `BRAIN_DATA_DIR`/`BRAIN_MODEL_CACHE_DIR`
  overrides, so the real per-user profile is never touched. Note the run is
  not footprint-free inside those overrides: `build_runtime()` opens storage
  before the tokenizer check that raises, so a modelless start leaves an
  empty bootstrapped `brain.sqlite3` behind (TASK-090 finding);
- a second pass runs the bare command with `BRAIN_DISABLE_SQLITE_VEC=1`
  (Linux) / `$env:BRAIN_DISABLE_SQLITE_VEC = '1'` (Windows): the switch
  forces the NumPy vector fallback for CI fallback testing and machines
  without the sqlite-vec wheel, and must not break startup — exit 3 with
  the typed model-not-installed error still holds.

## Linux

```bash
installer/linux/check-wheel-install.sh
```

## macOS

```bash
installer/macos/check-wheel-install.sh   # thin wrapper; shares the Linux bash source
```

## Windows

```powershell
pwsh installer/win/check-wheel-install.ps1
```

## Harness connectors

Nothing here. Harness setup is a command of the installed tool:

```
another-brain connect claude-code
```

It registers the MCP server (stdio) and installs the skill on every OS,
from the wheel alone — no repo clone, no Node, no shell. The POSIX-only
`connect.sh` + `harnesses/*.sh` connectors this directory used to carry were
retired in TASK-088; they registered a Streamable HTTP endpoint that assumed
a running `another-brain serve --http`, which the zero-server runtime does
not have. See `docs/deployment.md` for the user-facing documentation and
`another_brain/services/harness_connect.py` for the registry.
