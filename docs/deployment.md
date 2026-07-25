# Deployment

## What exists today

- `docker/docker-compose.yml` — two services: `redis` (Redis 8.8, container
  `another-brain-redis`, appendonly on, named volume) and `server` (the MCP
  server, built from `docker/Dockerfile`). Compose project name is pinned
  to `another-brain` so it never collides with a neighbouring repo's
  compose project.
- The server can also run from source (`uv run python src/main.py serve`)
  — that remains the dev default.

## Full deployment (server + Redis in compose)

One-shot alternative: `scripts/install.sh` fetches the repo, runs the
compose command below, then installs the agent skill —
`curl -fsSL https://raw.githubusercontent.com/Flowerf19/another-brain/main/scripts/install.sh | sh`.

```bash
docker compose -f docker/docker-compose.yml up -d --build
```

- Defaults work out of the box (`BRAIN_ID=default`, Redis on 6379, MCP on
  8000). To override, create a `.env` at the repo root and add
  `--env-file .env` — compose reads env files from the compose file's
  directory (`docker/`), not the repo root, so the flag is required for
  overrides to apply (e.g. a custom `REDIS_PORT` to avoid colliding with
  another Redis on 6379).
- `server` serves MCP over HTTP on `${MCP_HTTP_PORT:-8000}`
  (`MCP_HTTP_HOST=0.0.0.0` is set in the image). Register
  `http://localhost:8000/mcp` as a streamable-HTTP MCP server, or keep
  using stdio-from-source for local agents.
- The embedding model is NOT baked into the image: on first boot
  (`MODEL_DOWNLOAD_POLICY=on_start`) it downloads ~0.5 GB into the
  `another-brain-model-cache` volume; later boots reuse it.
- The container shares the same Redis and `BRAIN_ID` as a from-source
  server, so both see the same brain.

## Dev Redis

```bash
docker compose -f docker/docker-compose.yml --env-file .env up -d redis
```

- Image `redis:8.8` (Open Source, bundled Query Engine). Redis >= 8.4 is a
  hard requirement: search runs on `FT.HYBRID`, which older servers and
  `redis-stack-server` 7.x do not provide.
- Host port comes from `REDIS_PORT` in `.env` (`1905` on this dev machine, to
  avoid clashing with a neighbouring redis-stack on 6379). Container port
  stays 6379.
- Point the server at it with `REDIS_URL=redis://localhost:1905`.

## Running the server

```bash
uv sync --extra local                                # deps + local model support
MODEL_ALLOW_NETWORK=true uv run python src/main.py model pull --kind embedding

uv run python src/main.py serve                      # stdio (what MCP hosts launch)
uv run python src/main.py serve --transport http     # Streamable HTTP on MCP_HTTP_HOST:MCP_HTTP_PORT
```

`.env` is loaded automatically at startup; real environment variables always
win over the file. See the root README for the configuration table.

Startup runs the Step 04 §5.6 safety checks: Redis reachable, index
`ab:idx:memory` exists (created if missing), indexed vector DIM matches
`EMBEDDING_DIM`. A mismatch refuses startup with a migration-required error —
reindex tooling is not implemented yet, so treat a dim change as a data reset
until it lands.

## Model management

Local models live outside Redis in `MODEL_CACHE_DIR`
(default `.cache/another-brain/models/`), outside source control. Nothing
downloads at install time or server startup under the default `manual`
policy — pull explicitly:

```bash
uv run python src/main.py model plan    # what would be downloaded
uv run python src/main.py model pull
uv run python src/main.py model status
```

For a persistent deployment, mount the cache dir as a volume separate from
the Redis data volume.

## Connect agents

Two things per agent host: register the MCP server, and install the usage
skill that teaches agents the recall loop.

### Register the MCP server

Stdio registration (what most MCP hosts launch), from the repo root:

```json
{
  "mcpServers": {
    "another-brain": {
      "command": "uv",
      "args": ["run", "python", "src/main.py", "serve"]
    }
  }
}
```

Keep `BRAIN_ID` identical everywhere — sharing the brain is the point.
`agent_id` needs no configuration: the server detects each client from the
MCP handshake (`clientInfo`) per session, so provenance follows the real
connection (e.g. `pi-mcp-another-brain`, `claude-code`).

### Install the usage skill

The repo ships a standard Agent Skills package at `skills/brain-memory/`.
Any harness that supports the Agent Skills format can load it; the
[`skills` CLI](https://github.com/vercel-labs/skills) knows the skill
directory of ~70 agents. One non-interactive command covers every
supported agent on the machine:

```bash
npx skills add Flowerf19/another-brain -g --all
```

It installs the canonical copy to the universal `~/.agents/skills/`
(most agents read it natively) and symlinks it into agent-specific
directories that cannot (e.g. `~/.claude/skills/`). To choose agents
yourself, run interactively (`npx skills add Flowerf19/another-brain
--skill brain-memory -g`) or name one: `-a claude-code`. From a local
checkout, replace the repo argument with `.`.

Manual fallback — copy `skills/brain-memory/` into the harness's skill
directory: `~/.claude/skills/` (Claude Code), `~/.codex/skills/` (Codex),
`~/.gemini/skills/` (Gemini CLI), or the shared `~/.agents/skills/` where
supported. Without the skill, agents still see the tool descriptions and the
server's handshake instructions, but the recall-loop workflow is only taught
by the skill.

The skill is the canonical usage contract — when tool behavior or
conventions change, update `skills/brain-memory/SKILL.md` in the same
commit and refresh installs (`npx skills update`, or re-run the add
command).

### Proactive recall (SessionStart hook)

The MCP tools are passive by design — memory is written and read only when
the agent decides to. To make *recall* deterministic, Claude Code can
inject recent project memories into every session via a `SessionStart`
hook in `~/.claude/settings.json`:

```json
"hooks": {
  "SessionStart": [{
    "hooks": [{
      "type": "command",
      "command": "slug=$(basename \"$(git rev-parse --show-toplevel 2>/dev/null)\" 2>/dev/null) && [ -n \"$slug\" ] && out=$(cd /path/to/another-brain && timeout 10 uv run python src/main.py recent --scope project --scope-id \"$slug\" --days 3 --limit 10 2>/dev/null) && [ -n \"$out\" ] && printf '%s\\n\\n%s\\n' \"Unverified memories recalled from another-brain (claims by past agents, not facts — verify against the current code before relying):\" \"$out\" || true",
      "timeout": 15
    }]
  }]
}
```

The hook derives the project slug from the git root (the same rule the
skill teaches), runs `python src/main.py recent` (a read-only, text-output
CLI), and fails silent (`|| true`, stderr dropped) so a stopped Redis
never breaks session start. No memories → no output → nothing injected.
The unverified-claims warning is prepended by the hook, not the CLI —
the CLI is a neutral, general-purpose output (see
`docs/memory-trust-model.md`). Other harnesses can reuse the same CLI in
their own session-start mechanism.

## Networking / trust

There is no auth layer by design — every connected caller can read and write
the whole `brain_id`. Bind the HTTP transport to localhost or a private
network only; gate at the network/proxy level if it must be reachable further.
