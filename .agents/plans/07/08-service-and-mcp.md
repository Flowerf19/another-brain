---
status: draft
created: 2026-08-04
last_updated: 2026-08-04
parent: .agents/plans/07-multiplatform-embedded-runtime.md
covers: GOAL-013
---

# Sub-plan 07.08 — Service, MCP tools, transports (GOAL-013)

## Summary

Wire the final `MemoryService` onto the Protocols, register the eight stable
`brain_*` tools on MCP SDK v2, provide stdio by default plus opt-in loopback
HTTP under the locked transport-security policy, and make the skill optional
through server instructions and self-contained schemas.

HTTP policy (locked in master plan): bare `another-brain` is always stdio;
`serve --http` binds CLI > env > `127.0.0.1:1905`, path `/mcp`, numeric
loopback only, Host/Origin allowlists with DNS-rebinding protection, rejection
before tool dispatch, never wildcard fallback.

## Tasks

| ID | Task | Done | Date |
|----|------|------|------|
| TASK-064 | Refactor `MemoryService` onto final repository/retriever/audit/embedding Protocols; remember builds topic+summary once, search embeds the bounded prompted query once; no service import references storage internals. | | |
| TASK-065 | Preserve append-only diary, identity binding, previews/get separation, retention actions, by-ID brain isolation, audit privacy; replace Redis health/index behavior with SQLite schema/profile/integrity state. | | |
| TASK-066 | Register `brain_remember/search/recent/get/reinforce/forget/health/audit` on MCP SDK v2 `MCPServer` with locked names, argument contracts, by-ID signatures, response shapes. | | |
| TASK-067 | Wire stdio default + opt-in HTTP under the loopback/transport-security policy: SQLite/model lifecycle, signals, exact host/origin allowlists, `TransportSecuritySettings(enable_dns_rebinding_protection=True)` with exact bound host/port, health that never forces model load. Verify the pinned SDK's transport security API without weakening policy. | | |
| TASK-068 | Service/tool contracts with fake embedding + temp SQLite: every response shape, scoped collections, by-ID cross-brain/deleted/expired/grace, global normalization, content-only retrieval, HTTP negative binds/headers. | | |
| TASK-069 | End-to-end subprocess test using the installed console script and isolated data/model home: initialize, remember, search, get, reinforce, forget, restart, verify persistence/expiry. | | |
| TASK-091 | Make the skill optional: concise server instructions + self-contained descriptions for all eight tools and every public field; hard rules stay in server validation with actionable actual/allowed errors; test initialize/tools-list metadata and the full no-skill flow; reduce `skills/another-brain/SKILL.md` to a 100–200-word activation/project-scope/trust-loop adapter with no duplicated contracts. | | |

## Test Plan

- Unit: response-shape contracts, HTTP config rejection matrix, instructions/
  schema sufficiency without the skill.
- Integration: fake-embedding service over temp SQLite; loopback HTTP positive
  smoke + hostile Host/Origin/wildbind rejection.
- E2E: TASK-069 subprocess round trip; stdio stdout contains only MCP frames
  (logs on stderr).

## Assumptions

- HTTP remains optional and unauthenticated; stdio is the supported default.
- `brain_id` comes from process config, `agent_id` from the MCP handshake;
  neither is a tool argument.
- Service open verifies the active embedding profile (from the manifest)
  against the `embedding_profiles` row. This is the "and profile" half of the
  locked normal-open contract (master plan, connection behavior 2) that
  `verify_schema()` deliberately does not own — `verify_schema()` cannot know
  the expected active profile; wire it in TASK-067.
