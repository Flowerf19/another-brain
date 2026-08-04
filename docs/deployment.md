# Native deployment

Another Brain is installed as the `another-brain` command. The same wheel runs
on Windows and Ubuntu; each operating system uses its own user-data and cache
directories, so the installations do not share or overwrite one another.

## Windows

Install Python 3.12+ and `uv`, clone the repository, then run:

```powershell
.\scripts\install.ps1
another-brain doctor
```

Pass `-SkipModel` for a fast code-only install and download the model later
with `another-brain model pull`.

Default storage locations are resolved with `platformdirs`. Override them when
you need an isolated test instance:

```powershell
$env:ANOTHER_BRAIN_DATA_DIR = "$PWD\.local-data"
$env:ANOTHER_BRAIN_MODEL_DIR = "$PWD\.local-model"
another-brain doctor
another-brain
```

## Ubuntu

```bash
./scripts/install.sh
another-brain doctor
```

Use `AB_SKIP_MODEL=1 ./scripts/install.sh` to defer the model download. Native
Ubuntu paths are independent from Windows paths even when the same repository
is checked out on both systems.

## MCP host registration

The preferred registration starts the installed command over stdio:

```json
{
  "mcpServers": {
    "another-brain": {
      "command": "another-brain",
      "args": []
    }
  }
}
```

Helpers preserve unrelated entries in host configuration files:

```powershell
.\scripts\connect.ps1 codex,cursor
```

```bash
./scripts/connect.sh codex cursor
```

## Model management

The model installer downloads a pinned q4 ONNX graph and tokenizer, verifies
all SHA-256 hashes, then publishes the completed model directory. The runtime
does not download anything while answering `doctor` or `model status`.

```text
another-brain model pull
another-brain model status
```

## Transports

`another-brain` and `another-brain serve` start stdio. Logs and diagnostics go
to stderr so stdout remains reserved for MCP frames.

`another-brain serve --http` starts the optional Streamable HTTP transport at
`http://127.0.0.1:1905/mcp`. `MCP_HTTP_HOST` accepts numeric loopback literals
only; wildcard, hostname, LAN, and public bindings are rejected.

## Backup and isolated testing

Stop all writers before copying `brain.sqlite3`, or use SQLite's backup API.
The database is the complete memory source of truth; model files can be
downloaded again.

For experiments, point `ANOTHER_BRAIN_DATA_DIR` and
`ANOTHER_BRAIN_MODEL_DIR` at disposable directories. This leaves the normal
installation untouched on both supported operating systems.
