---
status: draft
created: 2026-08-04
last_updated: 2026-08-05
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
| TASK-064 | Refactor `MemoryService` onto final repository/retriever/audit/embedding Protocols; remember builds topic+summary once, search embeds the bounded prompted query once; no service import references storage internals. | ✅ | 2026-08-05 |
> Landed 2026-08-05 as `services/memory_service.py`. Not a refactor — the
> legacy service lives only on `main` (Redis, async because redis-py is async
> I/O), so this is a fresh implementation reading that as the behavior oracle.
> Sync throughout: SQLite and ONNX Runtime are both blocking, and every
> Protocol below is sync, so async here would be a wrapper that awaits nothing.
>
> Deviations from the legacy shape, all deliberate: documents embed
> `topic + summary` (locked input version 2, the reason the clean branch beats
> the oracle on Recall@5) rather than legacy summary-only; token budgets
> replace the character-count `CONTENT_MAX_CHARS` cap, and are checked *before*
> the embed so a doomed call never pays for a model load; `health()` reports
> SQLite/profile state instead of Redis ping and index metadata, and never
> forces a model load — lazy `not_loaded` is healthy, only a recorded load
> error degrades. `profile_id` is filled from `MODEL_MANIFEST` and never
> exposed on the tool surface.
>
> `domain/timeline.py` is now wired: `remember` derives `timeline_day` once at
> write time from `AppConfig.timeline_timezone`, and `_record_audit` uses the
> same helper, so the memory and audit write paths cannot disagree about which
> day a mutation belongs to.
>
> One structural change came with it: `ttl_ms_for`/`expires_at_ms_for` moved to
> `domain/retention.py` (the layout the master plan already specifies). They
> were in `services/sql/ttl.py`, which imports `SQLiteConnectionFactory` — the
> service must arm a TTL at write time, and importing that module would have
> pulled storage internals across the boundary this task forbids. `ttl.py`
> re-exports them for storage callers, so there is still one definition.
>
> Tests deferred to the final pass; verified by a temp-SQLite + fake-embedder
> round trip covering remember/get/recent/search/reinforce/forget/audit/health,
> global scope pinning, and the user-without-`scope_id` rejection.
| TASK-065 | Preserve append-only diary, identity binding, previews/get separation, retention actions, by-ID brain isolation, audit privacy; replace Redis health/index behavior with SQLite schema/profile/integrity state. | ✅ | 2026-08-05 |
> Landed 2026-08-05. All 12 scenarios in the TASK-031 oracle export
> (`tests/fixtures/legacy-baseline/behavior-v1.json`) replay green against the
> clean `MemoryService` — identity binding, append-only writes, TTL by
> importance, expired exclusion, reinforce re-arm, forget/restore/hard-delete
> lifecycle, recent ordering, audit privacy, preview/get separation, health
> shape, and both retrieval-behavior cases.
>
> Two scenarios pass by *differing* from the oracle, as TASK-008 records:
> `recent-ordering` (legacy sorts `period_start DESC` with ties in index order;
> clean locks `created_at DESC, memory_id ASC`) and
> `legacy-cosine-gate-drops-content-match`, where the legacy universal cosine
> gate returned `[]` and the clean branch returns the content match — the bug
> this rebuild exists to fix.
>
> The health half needed new surface: `StorageState` + `StorageHealthProbe` in
> `protocols.py`, implemented by `services/sql/health.py`. The Protocol is
> backend-neutral by construction — no PRAGMA names, no file paths, no
> extension identifiers beyond the `vector_backend` label retrieval already
> exposes — so the service still imports no storage internals. `health()` now
> degrades on schema mismatch, missing tables, a stored profile that does not
> match the locked manifest (an incomplete re-embedding must not let
> mixed-profile search start), or a failed integrity check. Integrity is
> opt-in via `deep=True`: `PRAGMA integrity_check` walks the whole database,
> so it belongs to `doctor`, not to a liveness answer. A database that cannot
> be opened is *reported* unhealthy rather than raised — health is asked
> precisely when storage is broken.
>
> Tests deferred to the final pass; the replay harness proving these 12 lands
> with them.
| TASK-066 | Register `brain_remember/search/recent/get/reinforce/forget/health/audit` on MCP SDK v2 `MCPServer` with locked names, argument contracts, by-ID signatures, response shapes. | ✅ | 2026-08-05 |
> Landed 2026-08-05 as `mcp/tools.py`. All eight locked names register and
> list; every tool carries a description; `brain_id`/`agent_id` appear in no
> input schema, so a caller can neither address another brain nor claim
> another identity. By-ID tools keep the `memory_id`-only signature and return
> the shared `not_found` shape.
>
> **Two SDK v2 API differences found by verifying against the installed
> package rather than the plan's wording.** `MCPServer` is imported from
> `mcp.server`, not from `mcp` top level. And the handshake field is
> `client_params.client_info`, not `.clientInfo` — v2 exposes the snake_case
> attribute and keeps the wire name only as a serialization alias. The legacy
> spelling silently yields `AttributeError`, so every call would have been
> attributed to the fallback agent id; caught because the check asserted the
> *expected* client name instead of merely that a name came back.
>
> Previews carry no relevance score: rank is list order, so no storage-vendor
> score encoding crosses the tool boundary. That is a deliberate departure
> from the oracle, whose preview included `relevance_score`/`score_source`.
>
> Verified with an in-memory `Client(server)` session — a real request context,
> unlike calling `server.call_tool()` directly, which has none. Round trip
> covered remember → search → get → recent → health → reinforce → audit →
> forget, plus unknown-id `not_found`, audit carrying no memory text, and
> validation errors reaching the client as `is_error=True` with actionable
> actual/allowed text (the TASK-091 no-skill requirement).
| TASK-067 | Wire stdio default + opt-in HTTP under the loopback/transport-security policy: SQLite/model lifecycle, signals, exact host/origin allowlists, `TransportSecuritySettings(enable_dns_rebinding_protection=True)` with exact bound host/port, health that never forces model load. Verify the pinned SDK's transport security API without weakening policy. | ✅ | 2026-08-05 |
> Landed 2026-08-05 as `mcp/server.py` (runtime assembly + both transports)
> and `services/sql/profile.py`; `cli.py` now serves instead of raising
> not-yet-available.
>
> **The SDK's loopback default is weaker than the locked policy.**
> `streamable_http_app` auto-enables DNS-rebinding protection when the bound
> host looks like loopback, but with `allowed_hosts=["127.0.0.1:*",
> "localhost:*", "[::1]:*"]`. Two gaps: `localhost` is a *name*, and a name is
> exactly what a rebinding attack controls; and `:*` accepts any port, so a
> different local service's Host header passes. Passing our own settings is
> therefore mandatory, not decorative — the allowlist is the exact bound
> authority (`127.0.0.1:1905`, `[::1]:1905` bracketed as it appears in a Host
> header) with no wildcard. Verified against the real middleware: correct Host
> 200; `localhost:PORT`, `evil.com`, and the right host on the wrong port all
> 421; hostile and `localhost` Origins 403 — all before tool dispatch.
>
> **Nothing registered the `embedding_profiles` row.** `memories.profile_id`
> is a FK into that table and the migration runner deliberately seeds no rows
> (it owns frozen DDL, not runtime facts), so every write would have failed the
> FK on a fresh database — only fixtures and benchmarks had ever inserted one.
> `register_profile()` fills the gap at service open, which is also the "and
> profile" half of the locked normal-open contract this sub-plan's Assumptions
> left to TASK-067. A stored profile that disagrees with the manifest is
> *refused*, not overwritten: silently re-pointing it would strand rows
> embedded under the old contract behind a claim that they match.
>
> Model loading stays lazy while storage opens eagerly — a broken database
> should fail at launch, but a stdio server spawned per session must not pay
> seconds and hundreds of MiB for sessions that never search. The tokenizer is
> the one exception (a few MB of vocabulary, no ONNX graph): budgets are
> checked before every embed, so an uninstalled profile surfaces at startup
> with the same actionable `model pull` message.
>
> Tests deferred to the final pass. Verified over real transports with a fake
> embedder: a stdio subprocess round trip (remember → search → get → recent →
> reinforce → audit → forget, preview/content separation, audit carrying no
> memory text and attributing the real handshake client, actionable validation
> errors) and a real loopback HTTP bind for the header matrix above. Also
> confirmed stdout stays byte-empty in stdio mode and health answers `ok` with
> `embedding_state: not_loaded`.
>
> Two stale assertions fell out and were corrected, not weakened: `serve` and
> the bare command no longer print "not yet available", and the wheel gate's
> fresh-install stderr check now asserts the `model pull` message (exit 3 is
> unchanged).

| TASK-068 | Service/tool contracts with fake embedding + temp SQLite: every response shape, collection operations in the bound brain, by-ID cross-brain/deleted/expired/grace, content-only retrieval, HTTP negative binds/headers. | | |
| TASK-069 | End-to-end subprocess test using the installed console script and isolated data/model home: initialize, remember, search, get, reinforce, forget, restart, verify persistence/expiry. | | |
| TASK-091 | Make the skill optional: concise server instructions + self-contained descriptions for all eight tools and every public field; hard rules stay in server validation with actionable actual/allowed errors; test initialize/tools-list metadata and the full no-skill flow; reduce `skills/another-brain/SKILL.md` to a 100–200-word activation/project-scope/trust-loop adapter with no duplicated contracts. | ✅ | 2026-08-05 |
> Landed 2026-08-05. Server instructions (136 words) ship on `MCPServer`;
> SKILL.md dropped 756 → 168 body words, keeping only what a tool schema
> cannot know: the mechanical `scope_id` derivation and the claims-not-truth
> stance.
>
> **Prose in a docstring never becomes a field description.** The SDK builds
> each input schema from the *signature*, so the argument guidance TASK-066
> wrote into docstrings reached the tool description and stopped there: all
> **29 fields across the eight tools carried a bare type and no description**.
> A client inspecting one argument — the normal case for a skill-less host —
> saw `scope_id: string` with nothing about it being required for user/project.
> Fixed with `Annotated[..., Field(description=...)]`; shared annotations
> (`Scope`, `ScopeId`, the filter types) keep one wording per concept rather
> than five drifting copies. Now 29/29.
>
> The numeric bounds that came with them (`ge`/`le` on importance, limit,
> min_importance, days) add a Pydantic layer *in front of* service validation,
> so it was worth confirming the errors stay actionable rather than becoming
> opaque schema noise. Verified over stdio: all nine invalid inputs are
> rejected with actual and allowed values, e.g. `importance Input should be
> less than or equal to 5 [input_value=9]`, while the service's own messages
> still surface for everything schema types cannot express
> (`scope must be one of user, project, global; got 'team'`).
>
> No-skill sufficiency checked as a property, not a vibe: 17 contract facts a
> client must know — scope rules, topic shape and token cap, importance→TTL,
> append-only, preview/detail seam, content-not-embedded, reinforce as the only
> renewal, forget's grace window, audit privacy, claims-not-truth, no secrets,
> degraded handling — are all reachable from instructions + descriptions alone.
> `brain_id`/`agent_id` remain absent from every schema.


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
  the expected active profile. **Wired in TASK-067 as
  `services/sql/profile.py`, which also registers the row: nothing had, and
  `memories.profile_id` is a FK into that table.**
