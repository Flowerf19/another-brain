# Deployment

Another Brain deploys as a single installed executable — there is no server,
container, or external database to run. The only requirement is Python
3.12+.

Install the published package into a venv with standard pip:

```bash
python -m venv .venv
.venv/bin/python -m pip install another-brain
```

Windows runs the same two commands with `.venv\Scripts\python`; the console
script lands in the venv's `bin` directory on POSIX and `Scripts` on
Windows. From a checkout of this repo, install the local tree from the repo
root instead — hatchling is the PEP 517 build backend, so pip needs nothing
beyond the checkout:

```bash
python -m venv .venv
.venv/bin/python -m pip install .
```

Neither path reads `uv.lock` or needs uv at install or runtime. `uv tool
install another-brain` remains an optional one-command convenience, and uv
with the lockfile is this repo's reproducible development workflow. With
the venv active, the commands below are identical:

```bash
another-brain model pull   # one-time, pinned + hash-verified
another-brain              # MCP stdio server for your harness
another-brain serve --http # optional loopback HTTP on 127.0.0.1:1905
```

Other live commands: `model status` (install state without loading the
model), `recent [--limit N]` (print the bound brain's newest entries),
`admin restore|hard-delete MEMORY_ID` (lifecycle administration),
`import-jsonl PATH` (JSONL v1 import), and `doctor` (health report).
`doctor` checks the platform support tier, resolved data/model paths,
package version, per-file model hashes, and the real database read-only
(integrity, foreign keys, schema version, journal mode, page size), then
runs an isolated bootstrap/write/read/delete/FTS5 probe in a throwaway
Temp database that never touches the real profile; it never loads the
embedding model, never downloads anything, and exits nonzero when any
check fails (missing model and missing database are warnings, not
failures — `recent`/`admin`/`connect` still work without them).

Data lives in the per-user data directory (`brain.sqlite3`); the model lives
in the per-user cache directory. Override with `BRAIN_DATA_DIR` /
`BRAIN_MODEL_CACHE_DIR`; `BRAIN_ID` selects the process-bound brain
(default `default`) and `TIMELINE_TIMEZONE` (IANA name, default `UTC`)
controls the diary day. Loopback HTTP binds with CLI `--host/--port` >
`MCP_HTTP_HOST`/`MCP_HTTP_PORT` > `127.0.0.1:1905`, numeric loopback only,
endpoint path `/mcp`.

`BRAIN_DISABLE_SQLITE_VEC` (truthy: `1`/`true`/`yes`/`on`, case-insensitive)
forces the NumPy exact vector fallback: the sqlite-vec extension is never
loaded, so retrieval, `doctor`, and health all take the same path a machine
without the sqlite-vec wheel takes. Intended for CI fallback testing and
users on platforms where the wheel is unavailable.

## Connecting a harness

`another-brain connect` does the whole setup for an agent harness — the same
command, with the same result, on Linux, macOS, and Windows. There is no
manual JSON to write, no repo to clone, and no Node/npx:

```bash
another-brain connect              # list known + detected harnesses
another-brain connect --detect     # detected names only, writes nothing
another-brain connect claude-code  # register the MCP server + install the skill
```

Known harnesses: `claude-code`, `codex`, `cursor`, `gemini-cli`, `pi`. A
harness counts as detected when its `~/.<name>` config dotdir exists — that
dotdir is the same path on every OS, so detection needs no platform
branching.

For each named harness the command does two idempotent things:

1. **Registers the MCP server as stdio.** The entry is always
   `{"command": "another-brain"}` — the installed executable *is* the MCP
   stdio server, so there is no url, port, or `serve --http` prerequisite.
   Harnesses that own their config through a CLI (`claude`, `codex`) are
   registered through that CLI; the rest get an upsert into their well-known
   config file (`~/.cursor/mcp.json`, `~/.gemini/settings.json`,
   `~/.config/mcp/mcp.json` for pi), preserving every other key and server.
   Every harness gets the same bare entry — the tools are named with bare
   verbs (`remember`, `search`, `get`, ...), and harness adapters that
   prefix tool names with the server name expose them as
   `another_brain_remember` and so on. If a harness's CLI is not
   on PATH, the command prints the exact entry to add by hand and exits
   nonzero instead of failing silently.
2. **Installs the skill** into the harness's skills directory
   (`~/.claude/skills/another-brain/` and equivalents), from the copy bundled
   inside the wheel. Re-runs replace the previous copy rather than nesting.

```
$ another-brain connect cursor pi
wrote ~/.cursor/mcp.json: mcpServers.another-brain = {"command": "another-brain"}
installed the skill for cursor -> ~/.cursor/skills/another-brain
wrote ~/.config/mcp/mcp.json: mcpServers.another-brain = {"command": "another-brain"}
installed the skill for pi -> ~/.pi/agent/skills/another-brain
```

With the tools renamed to bare verbs, each harness adds its own prefix on
top of the wire names: the pi adapter exposes `another_brain_health`, Claude
Code shows `mcp__another-brain__health`, and the MCP wire contract itself is
unchanged.

Restart the harness afterwards so it picks up the new server. `connect` needs
neither the model nor a database, so it is safe to run immediately after
install — the model download can come later.

The full platform matrix and support tiers land with the release notes;
`another-brain doctor` already reports the tier for the machine it runs on.
