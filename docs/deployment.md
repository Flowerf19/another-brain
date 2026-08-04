# Deployment

Another Brain deploys as a single installed executable — there is no server,
container, compose stack, or external database to run.

```bash
uv tool install another-brain
another-brain model pull   # one-time, pinned + hash-verified
another-brain              # MCP stdio server for your harness
```

Data lives in the per-user data directory (`brain.sqlite3`); the model lives
in the per-user cache directory. Override with `BRAIN_DATA_DIR` /
`BRAIN_MODEL_CACHE_DIR`. Optional loopback HTTP: `another-brain serve --http`
(numeric loopback binds only, default `127.0.0.1:1905`).

Full platform matrix, harness connector setup, and troubleshooting land with
the release gate (TASK-086/088).
