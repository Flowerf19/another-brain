# Per-OS install gates

Wheel install verification, split by operating system. Each gate builds the
wheel into a temp workspace, installs it into a fresh venv, proves the package
imports **from the venv** (checked from a neutral working directory so the
checkout's root `another_brain/` cannot shadow it), and verifies the typed
CLI contract on a machine with no model installed:

- bare `another-brain` exits 3 with `model is not installed` on stderr —
  never a traceback;
- no per-user data directory is created as a side effect;
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

`installer/linux/connect.sh` (macOS: `installer/macos/connect.sh`, same
script) registers the Another Brain MCP server into a harness's own config
and installs the another-brain skill — per-harness logic lives one file per
harness in `installer/linux/harnesses/`. One script serves both OSes because
every supported harness keeps its CLI config in the same `$HOME/.<name>`
dotdir on Linux and macOS alike (the `~/Library/Application Support`
convention only applies to native GUI apps — and to our own data dir, which
platformdirs handles inside the Python code, not here). POSIX-only; the
Windows story is TASK-088 (documented MCP config JSON). NOTE: connectors
currently register the Streamable HTTP endpoint (`http://localhost:1905/mcp`),
which assumes a running `another-brain serve --http` — TASK-088 decides the
move to stdio (`{"command": "another-brain"}`), which is also the
Windows-friendly form.
