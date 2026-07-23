# Agent Docs

Read order for agents working in this repo:

1. `README.md` at the repo root - public overview, quick start, configuration.
2. `.agents/plans/another-brain-architecture.md` - canonical architecture plan
   (source of truth for product and technical decisions).
3. `.agents/plans/01-architecture-foundation.md` ... `04-...md` - approved step
   contracts; `05-redis-hybrid-search.md` - `FT.HYBRID` mechanism explainer;
   `06-agent-usage-guidance.md` - draft: distributing usage guidance to agents.
4. `.agents/PROJECT_CONTEXT.md` - concise boundaries and runtime state.
5. `.agents/AGENT_RULES.md` - implementation rules and unsafe shortcuts to avoid.
6. `.agents/TESTING_GUIDE.md` - test commands and the integration Redis contract.
7. `docs/architecture.md`, `docs/mcp-tools.md`, `docs/deployment.md` - public
   module map, tool surface, and deployment notes.

This repo is the standalone home for **Another Brain**, a memory service for
MCP-capable agent systems. It must remain independent from March7/Evernight;
March7's T2 diary code is only a reference for timeline chunking behavior.

Current repo state:

- The MVP service is implemented and tested: 8 `brain_*` MCP tools over
  stdio/HTTP, Redis 8.8 storage with `FT.HYBRID` search, importance TTL,
  soft delete + audit, local Harrier embedding with model install policy.
- `uv run pytest` runs 183 unit + 14 integration tests (integration needs the
  compose Redis on `REDIS_PORT`).
- Not yet implemented: `server/resources.py`, `storage/migrations.py`,
  `memory/normalization.py`, external embedding providers (only `local`),
  `packages/npm-launcher`, the service Dockerfile, `brain_ingest`.

Do not invent implementation facts that are not present in the repo. When
changing commands, env vars, runtime boundaries, or storage contracts, update
these docs in the same change.
