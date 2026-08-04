# Another Brain

**v0.11.1**

Another Brain is a native, local MCP memory service for AI agents. It runs as
one installed Python command on Windows and Ubuntu, stores data in SQLite/FTS5,
and generates multilingual embeddings with ONNX Runtime CPU.

The default transport is stdio, so there is no background service to manage.
Windows and Ubuntu use the same Python package while keeping their own native
data and model-cache directories.

## Features

- Shared diary memory for Claude Code, Codex, Cursor, Gemini CLI, and other MCP
  hosts.
- SQLite source of truth with durable expiry, soft deletion, and audit events.
- Hybrid FTS5 keyword and exact vector retrieval, fused with RRF.
- Pinned Harrier 270M q4 ONNX model for Vietnamese and English.
- Eight stable MCP tools: `brain_remember`, `brain_search`, `brain_recent`,
  `brain_get`, `brain_reinforce`, `brain_forget`, `brain_health`, and
  `brain_audit`.

## Install on Windows

Requirements: Python 3.12+ and
[uv](https://docs.astral.sh/uv/getting-started/installation/).

```powershell
git clone <this-repository-url>
Set-Location another-brain
.\scripts\install.ps1
```

To install without downloading the approximately 0.5 GB model immediately:

```powershell
.\scripts\install.ps1 -SkipModel
another-brain model pull
```

Register the installed stdio command with one or more MCP hosts:

```powershell
.\scripts\connect.ps1 codex
.\scripts\connect.ps1 claude-code,cursor
```

## Install on Ubuntu

```bash
git clone <this-repository-url>
cd another-brain
./scripts/install.sh
./scripts/connect.sh codex
```

Set `AB_SKIP_MODEL=1` when running `install.sh` to defer the model download.

## Run and verify

```text
another-brain doctor
another-brain model status
another-brain
```

Running `another-brain` with no subcommand starts MCP over stdio. For a local
HTTP endpoint instead:

```text
another-brain serve --http
```

HTTP is restricted to numeric loopback addresses. The default endpoint is
`http://127.0.0.1:1905/mcp`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `BRAIN_ID` | `default` | Local namespace stored in the database |
| `ANOTHER_BRAIN_DATA_DIR` | OS user-data directory | Parent directory for `brain.sqlite3` |
| `ANOTHER_BRAIN_DATABASE` | `<data-dir>/brain.sqlite3` | Explicit SQLite file path |
| `ANOTHER_BRAIN_MODEL_DIR` | OS user-cache directory | Verified ONNX model artifacts |
| `TIMELINE_TIMEZONE` | `Asia/Ho_Chi_Minh` | Timeline-day and response timezone |
| `MCP_HTTP_HOST` | `127.0.0.1` | Numeric loopback address only |
| `MCP_HTTP_PORT` | `1905` | Optional HTTP port |
| `AUDIT_RETENTION_DAYS` | `90` | Audit-event retention |
| `FORGET_GRACE_SECONDS` | `2592000` | Restore window after soft deletion |

## Development

```text
uv sync --locked
uv run pytest --cov=another_brain --cov-branch --cov-report=term-missing --cov-fail-under=90
uv build --no-sources
```

The standard suite uses deterministic fake embeddings and skips only the
downloaded-model gate. To exercise the pinned ONNX files as well:

```powershell
$env:ANOTHER_BRAIN_TEST_MODEL_DIR = "C:\path\to\model"
uv run pytest -m slow
```

```bash
ANOTHER_BRAIN_TEST_MODEL_DIR=/path/to/model uv run pytest -m slow
```

See [deployment](docs/deployment.md), [MCP tool contracts](docs/mcp-tools.md),
[architecture](docs/architecture.md), and the
[memory trust model](docs/memory-trust-model.md).

## License

[MIT](LICENSE). The embedding model retains its own license.
