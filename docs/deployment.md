# Deployment

Another Brain deploys as a single installed executable — there is no server,
container, or external database to run.

```bash
uv tool install another-brain
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

Full platform matrix, harness connector setup, and troubleshooting land with
the release gate (GOAL-016).
