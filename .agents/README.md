# Agent Docs

Read order for agents working in this repo:

1. `README.md` at the repo root - public overview, quick start, configuration.
2. `.agents/plans/another-brain-architecture.md` - canonical architecture plan
   (source of truth for product and technical decisions).
3. `.agents/plans/01-architecture-foundation.md` ... `04-...md` - approved step
   contracts; `05-redis-hybrid-search.md` - `FT.HYBRID` mechanism explainer;
   `06-agent-usage-guidance.md` - done: distributing usage guidance to agents.
4. `.agents/PROJECT_CONTEXT.md` - concise boundaries and runtime state.
5. `.agents/AGENT_RULES.md` - implementation rules and unsafe shortcuts to avoid.
6. `.agents/TESTING_GUIDE.md` - test commands and the integration Redis contract.
7. `docs/architecture.md`, `docs/mcp-tools.md`, `docs/deployment.md` - public
   module map, tool surface, and deployment notes.
8. `docs/memory-trust-model.md` - epistemic contract: memories are claims,
   not facts. Read before changing recall, injection, or ingest behavior.

This repo is the standalone home for **Another Brain**, a memory service for
MCP-capable agent systems. It must remain independent from March7/Evernight;
March7's T2 diary code is only a reference for timeline chunking behavior.

Current repo state:

- The MVP service is implemented and tested: 8 `brain_*` MCP tools over
  stdio/HTTP, Redis 8.8 storage with `FT.HYBRID` search, importance TTL,
  soft delete + audit, local Harrier embedding with model install policy.
- `uv run pytest` runs 190 unit + 14 integration tests (integration needs the
  compose Redis on `REDIS_PORT`).
- Not yet implemented: `server/resources.py`, `storage/migrations.py`,
  external embedding providers (only `local`), `brain_ingest`.
- Cut by decision (2026-07-23): the server-side memory model —
  normalization is the calling agent's job (architecture plan, milestone 2).
- Cut by decision (2026-07-25): the npm launcher — Docker is the only
  install shape; hosts connect via stdio or Streamable HTTP directly.

Do not invent implementation facts that are not present in the repo. When
changing commands, env vars, runtime boundaries, or storage contracts, update
these docs in the same change.
