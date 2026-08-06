# Per-OS install gates

Wheel install verification, split by operating system. Each gate builds the
wheel into a temp workspace, installs it into a fresh venv, proves the package
imports **from the venv** (checked from a neutral working directory so the
checkout's root `another_brain/` cannot shadow it), and verifies the typed
CLI contract on a machine with no model installed:

- bare `another-brain` exits 3 with `model is not installed` on stderr —
  never a traceback;
- no per-user data directory is created as a side effect.

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
