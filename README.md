# Another Brain

Long-term memory your AI coding agents actually share. One brain, many agents:
what Claude Code learns on Monday, Codex can recall on Friday.

It runs as a single installed executable — no server to start, no container,
no database to administer, and nothing leaves your machine. Memories live in
one SQLite file in your user directory; embeddings are computed locally on
CPU.

## Install

The one prerequisite is [uv](https://docs.astral.sh/uv/) — no daemon, no
root, no container runtime:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh              # Linux / macOS
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows
```

Then:

```bash
uv tool install another-brain
another-brain model pull            # one-time ~206 MB embedding model, hash-verified
another-brain connect claude-code   # set up your agent harness
```

Restart the harness and your agent has memory. `connect` writes the MCP
server entry into the harness's own config and installs a skill that teaches
the agent when to use it — no JSON to edit by hand, on any OS.

Run `another-brain connect` with no arguments to see which harnesses are
known and which are installed here. Supported today: `claude-code`, `codex`,
`cursor`, `gemini-cli`, `pi`.

## What your agent can do

Eight tools appear in the agent's toolbox:

| Tool | What it does |
|---|---|
| `brain_remember` | store one thing worth recalling later — a decision, a bug and its fix, a preference |
| `brain_search` | find memories by meaning *and* keywords at once |
| `brain_recent` | list the newest entries, or walk one day or one topic |
| `brain_get` | fetch one memory in full |
| `brain_reinforce` | a memory proved useful — keep it longer |
| `brain_forget` | a memory proved wrong — drop it |
| `brain_health` | is the brain reachable and which one is bound |
| `brain_audit` | what changed, when, and by which agent — never the memory text |

Memory here is a **diary that forgets on purpose.** Each entry gets a
lifespan from its importance — 1 to 5 maps to 7, 30, 90, 180, or 365 days —
and expires unless an agent reinforces it after actually using it. Nothing
accumulates forever, and a memory that turns out to be wrong can be
forgotten. Forgetting is soft for 30 days, so a mistake is recoverable.

Search combines two independent signals: semantic similarity (so "how do we
handle expired tokens" finds a note about refresh logic) and full-text
keyword match (so an exact error string or file path is findable verbatim).

## New in 0.11.0

- Redis and Docker are gone: one executable and one SQLite file, nothing running in the background.
- `another-brain connect` sets up any supported harness on Linux, macOS, and Windows with one command.
- `another-brain doctor` checks your install, model hashes, and database, and tells you what to fix.

## Commands

| Command | Purpose |
|---|---|
| `another-brain` | the MCP server itself (your harness runs this; you normally don't) |
| `another-brain connect [harness…]` | register the server + install the skill |
| `another-brain model pull` / `model status` | download or check the embedding model |
| `another-brain recent [--limit N]` | print the newest entries from the terminal |
| `another-brain doctor` | full health report; exits nonzero if something is wrong |
| `another-brain admin restore\|hard-delete ID` | undo a forget inside its grace window, or purge |
| `another-brain import-jsonl PATH` | import a JSONL v1 export |
| `another-brain serve --http` | optional loopback HTTP on 127.0.0.1:1905 instead of stdio |

`recent`, `admin`, `connect`, and `doctor` all work without the model
installed.

## Your data

Memories live in `brain.sqlite3` in your per-user data directory, and the
model in your per-user cache directory — `another-brain doctor` prints both
exact paths. Nothing is uploaded; after `model pull` the tool never needs the
network again.

| Variable | Effect |
|---|---|
| `BRAIN_DATA_DIR` | where `brain.sqlite3` lives |
| `BRAIN_MODEL_CACHE_DIR` | where the model lives |
| `BRAIN_ID` | which brain this process is bound to (default `default`) |
| `TIMELINE_TIMEZONE` | IANA zone deciding the diary day (default `UTC`) |

Each agent process loads its own copy of the embedding model, about 322 MiB
of RAM once it has embedded something — worth knowing if you run several
harnesses at once.

## Platform support

Gated in CI on Linux x86_64, macOS 14+ Apple Silicon, and Windows x86_64,
with Python 3.12–3.14. Linux ARM64 and Windows ARM64 work but have no CI
hardware. macOS Intel, macOS 13 and older, and Alpine/musl are not supported
— the install fails clearly rather than silently building from source.
`another-brain doctor` reports the tier for your machine. Full matrix in
[CHANGELOG.md](CHANGELOG.md).

## More

- [CHANGELOG.md](CHANGELOG.md) — release notes, support matrix, measured performance
- [docs/deployment.md](docs/deployment.md) — harness setup in detail, configuration
- [docs/mcp-tools.md](docs/mcp-tools.md) — the tool contracts
- [docs/memory-trust-model.md](docs/memory-trust-model.md) — how much to trust a recalled memory

MIT licensed.
