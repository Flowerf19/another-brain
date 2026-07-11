# Agent Docs

Read order for agents working in this repo:

1. `README.md` at the repo root - public project overview.
2. `.agents/plans/01-architecture-foundation.md` - current small review slice.
3. `.agents/plans/02-directory-and-class-architecture.md` - proposed runtime
   folders, module names, and core class names.
4. `.agents/plans/03-model-install-policy.md` - model download, cache, and
   install policy.
5. `.agents/plans/another-brain-architecture.md` - canonical architecture plan.
6. `.agents/PROJECT_CONTEXT.md` - concise project boundaries and architecture.
7. `.agents/AGENT_RULES.md` - implementation rules and unsafe shortcuts to avoid.
8. `.agents/TESTING_GUIDE.md` - current test status and expected test shape.

This repo is the standalone home for **Another Brain**, a memory service for
MCP-capable agent systems. It should remain independent from March7/Evernight;
March7's T2 diary code is only a reference for timeline chunking behavior.

Current repo state:

- `README.md` exists and links to the architecture plan.
- `.agents/plans/01-architecture-foundation.md` contains the current first
  architecture review slice.
- `.agents/plans/02-directory-and-class-architecture.md` contains the proposed
  runtime folder and class architecture.
- `.agents/plans/03-model-install-policy.md` contains the proposed model
  download and cache policy.
- `.agents/plans/another-brain-architecture.md` contains the architecture plan.
- `.agents/` contains these guidance docs.
- Placeholder `src/`, `docs/`, `docker/`, `packages/`, and `tests/` paths exist.
- No runtime implementation, package manifest, Docker config, or executable test
  suite exists yet.

Do not invent implementation facts that are not present in the repo. When adding
source code later, update these docs in the same change if commands, env vars,
runtime boundaries, or storage contracts change.
