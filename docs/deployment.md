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
`admin restore|hard-delete MEMORY_ID` (lifecycle administration), and
`import-jsonl PATH` (JSONL v1 import). `doctor` remains a typed
not-yet-available stub until the release gate (GOAL-016).

Data lives in the per-user data directory (`brain.sqlite3`); the model lives
in the per-user cache directory. Override with `BRAIN_DATA_DIR` /
`BRAIN_MODEL_CACHE_DIR`; `BRAIN_ID` selects the process-bound brain
(default `default`) and `TIMELINE_TIMEZONE` (IANA name, default `UTC`)
controls the diary day. Loopback HTTP binds with CLI `--host/--port` >
`MCP_HTTP_HOST`/`MCP_HTTP_PORT` > `127.0.0.1:1905`, numeric loopback only,
endpoint path `/mcp`.

Full platform matrix, harness connector setup, and troubleshooting land with
the release gate (GOAL-016).
