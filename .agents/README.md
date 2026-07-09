# Agent Docs

Read order for agents working in this repo:

1. `README.md` at the repo root - public project overview.
2. `.agents/plans/another-brain-architecture.md` - canonical architecture plan.
3. `.agents/PROJECT_CONTEXT.md` - concise project boundaries and architecture.
4. `.agents/AGENT_RULES.md` - implementation rules and unsafe shortcuts to avoid.
5. `.agents/TESTING_GUIDE.md` - current test status and expected test shape.

This repo is the standalone home for **Another Brain**, a memory service for
MCP-capable agent systems. It should remain independent from March7/Evernight;
March7's T2 diary code is only a reference for timeline chunking behavior.

Current repo state:

- `README.md` exists and links to the architecture plan.
- `.agents/plans/another-brain-architecture.md` contains the architecture plan.
- `.agents/` contains these guidance docs.
- No runtime source tree, package manifest, Docker files, or tests exist yet.

Do not invent implementation facts that are not present in the repo. When adding
source code later, update these docs in the same change if commands, env vars,
runtime boundaries, or storage contracts change.
