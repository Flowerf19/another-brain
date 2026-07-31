# Another Brain

**v0.11.0**

> **Development-branch notice:** `v0.11.0` is being rebuilt as a standalone
> SQLite/FTS5/ONNX tool with no Docker or Redis. The runnable commands below
> still describe the legacy implementation preserved on `main` (`edc0e57`)
> until the clean wheel lands. For implementation, use the
> [approved target architecture](.agents/plans/another-brain-architecture.md)
> and [Plan 07](.agents/plans/07-multiplatform-embedded-runtime.md), not the
> legacy deployment instructions.

One shared long-term memory for all your AI agents — a self-hosted MCP
server with hybrid search, local multilingual embeddings, and
self-expiring memory.

## What it does

Each AI agent remembers nothing or remembers in its own silo: a bug
fixed in a Claude session is invisible to Codex the next morning.
Another Brain is one small service that every agent connects to over
MCP. Whatever one agent stores — a decision, a fix, a preference — any
other agent on the same `brain_id` can recall it later, in its own
session, days or weeks on.

- **Shared by design** — one brain for Claude Code, Codex, pi, bots;
  `agent_id` is recorded as provenance, not a partition.
- **Remembers like a diary** — dated entries with a topic and a short
  summary; append-only, an update is a new entry plus a forget.
- **Forgets on purpose** — every entry carries an importance-derived
  TTL (7–365 days); only an explicit reinforce after real use renews
  it. The system fails toward forgetting, never toward bloat.
- **Recall is claims, not facts** — search returns what past agents
  wrote down, unverified; see the
  [trust model](docs/memory-trust-model.md).
- **Fully local** — Redis 8.8 hybrid search (BM25 + vector fused in one
  `FT.HYBRID` call) and a 270M multilingual embedding model (Vietnamese
  + English) that runs offline; footprint under ~1 GB.

Agents interact through eight `brain_*` tools:

| Tool | Purpose |
| --- | --- |
| `brain_remember` | Store a memory (topic + 1-2 sentence summary, optional detail) |
| `brain_search` | Hybrid semantic + keyword search, preview lines only |
| `brain_recent` | Newest entries on the timeline, no query needed |
| `brain_get` | Full detail for one memory |
| `brain_reinforce` | Renew TTL after a memory proved useful — the only renewal |
| `brain_forget` | Soft-delete what proved wrong (30-day admin-recoverable grace) |
| `brain_health` | Service + index + embedding status |
| `brain_audit` | Secret-free mutation trail (who/what/when, never the text) |

## Quick start

Requirements: `git`, Docker with the compose plugin (Linux permission
denied → `sudo usermod -aG docker $USER`, re-login), optionally Node.js
>= 18 for the skill step.

One command installs Redis 8.8 + the MCP server, then asks which
detected agent harnesses get the `another-brain` skill:

```bash
curl -fsSL https://raw.githubusercontent.com/Flowerf19/another-brain/main/scripts/install.sh | sh
```

Or by hand:

```bash
git clone <this repo> && cd another-brain
docker compose -f docker/docker-compose.yml up -d --build
```

Defaults work out of the box (`BRAIN_ID=default`, Redis on 1906, MCP on
1905); create a `.env` and pass `--env-file .env` only to override.
Server endpoint: `http://localhost:1905/mcp`. First boot downloads the
embedding model (~0.5 GB) into a Docker volume.

## Connect your agents

Per-harness setup — registers the MCP server in the harness's own
config **and** installs the `another-brain` skill that teaches agents
the recall loop:

```bash
scripts/connect.sh                    # list known + detected harnesses
scripts/connect.sh claude-code codex  # connect the ones you use
```

Supported: `claude-code`/`codex` (native CLIs), `gemini-cli`/`cursor`
(JSON merge), `pi` (upserts the shared `~/.config/mcp/mcp.json` that the
pi-mcp-adapter extension reads). Other hosts: register
`http://localhost:1905/mcp` (or stdio from a checkout — see
[`docs/deployment.md`](docs/deployment.md)) and install the skill with
`npx skills add Flowerf19/another-brain -g -y`.

Without the skill the tools still work; agents only discover the
workflow from it.

## Configuration

All settings are environment variables (`.env` auto-loaded; real env
wins). Every inconsistency is a startup error. The useful subset:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BRAIN_ID` | `default` | Brain namespace this server writes to |
| `REDIS_URL` | `redis://localhost:1905` | Redis >= 8.4; the compose Redis maps host port **1906** — set this when running from source |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | `microsoft/harrier-oss-v1-270m` / `640` | Local embedding model |
| `MODEL_DOWNLOAD_POLICY` | `manual` | `disabled` / `manual` / `lazy` / `on_start` |
| `SEARCH_TOP_K` / `SEARCH_MIN_COSINE` | `20` / `0.30` | Recall page size and quality floor |
| `TTL_IMPORTANCE_5`...`TTL_IMPORTANCE_1` | 365d...7d | All-or-none override set |
| `FORGET_GRACE_SECONDS` | `2592000` | Soft-delete recovery window |
| `TIMELINE_TIMEZONE` | `Asia/Ho_Chi_Minh` | `timeline_day` derivation |
| `MCP_HTTP_HOST` / `MCP_HTTP_PORT` | `127.0.0.1` / `1905` | HTTP transport bind |

Changing `EMBEDDING_DIM` against an existing index refuses startup (no
reindex tooling yet).

## Safety & trust

- **No auth layer, by design.** Every connected caller can read and
  write the whole brain. Bind the HTTP transport to localhost or a
  private network only.
- **Memories are claims, not facts.** Treat recall like advice from a
  colleague, not ground truth:
  [`docs/memory-trust-model.md`](docs/memory-trust-model.md).
- **No secrets in memory or audit.** The audit trail records actions
  and ids, never memory text; don't store credentials in memories
  either.

## For agents and developers

Deeper documentation lives in the repo, by audience:

- Operating the service: [`docs/deployment.md`](docs/deployment.md) —
  manual install, model management, per-harness connectors, networking.
- Tool parameter contracts: [`docs/mcp-tools.md`](docs/mcp-tools.md).
- Why memories are claims, not facts:
  [`docs/memory-trust-model.md`](docs/memory-trust-model.md).
- Module map: [`docs/architecture.md`](docs/architecture.md).
- Working in this repo (read order, source layout, testing, rules,
  architecture plans): start with
  [`.agents/README.md`](.agents/README.md). A source checkout is only
  needed to hack on the service itself: `uv sync --extra local`.

## License

[MIT](LICENSE) — Redis and the embedding model keep their own licenses.
