# MCP Tools

Server name: `another-brain`. Eight tools, implemented in
[`src/server/tools.py`](../src/server/tools.py). `brain_id` and `agent_id` are
bound from server config — no tool accepts identity input.

Conventions shared by all tools:

- `scope` is `user | project | global`. `scope_id` is required for `user` and
  `project`; `scope=global` pins `scope_id="global"` and may omit it.
- Timestamps in responses are ISO 8601 in the configured `TIMELINE_TIMEZONE`.
- `topic` and `catalog` are lowercase-kebab slugs. `catalog` is an open
  vocabulary (starter set: `bug`, `decision`, `preference`, `task`, `fact`,
  `note`).

## brain_remember

Append one diary entry. The store is append-only — no merge, no update.

| Param | Required | Notes |
| --- | --- | --- |
| `topic` | yes | slug labeling the entry |
| `summary` | yes | 1-2 sentences; the canonical text — this is what gets embedded |
| `scope` (+ `scope_id`) | yes | see conventions |
| `catalog` | no | default `note` |
| `content` | no | optional detail/checklist, BM25-searchable, never embedded, max `CONTENT_MAX_CHARS` (4000) |
| `importance` | no | 1-5, sets TTL: 5=365d, 4=180d, 3=90d, 2=30d, 1=7d |
| `metadata` | no | JSON object, provenance only |

Returns `memory_id`, `timeline_day`, `expires_at`.

## brain_search

Hybrid semantic + BM25 search (one `FT.HYBRID` call, RRF-fused in Redis, cosine
floor applied before the top-k cut). Returns preview lines only: `memory_id`,
`topic`, `catalog`, `summary`, `timeline_day`, `importance`, `has_content`,
`relevance_score`, `score_source`. Never returns `content` or embeddings.

| Param | Required | Notes |
| --- | --- | --- |
| `query` | yes | non-empty; a query with no lexical terms degrades to KNN-only |
| `scope` (+ `scope_id`) | yes | see conventions |
| `topic`, `catalog` | no | exact TAG filters |
| `timeline_day` | no | `YYYY-MM-DD` |
| `min_importance` | no | 1-5 |
| `days` | no | only memories from the last N days |

## brain_recent

Timeline listing, newest first (sort by `period_start`), same preview shape as
`brain_search` without scores. Params: `scope` (+`scope_id`), optional
`topic`, `catalog`, `timeline_day`, `min_importance`, `days`, `limit`
(default `SEARCH_TOP_K`, max 100).

## brain_get

Full record by `memory_id`: everything in the preview plus `content`, `scope`,
`scope_id`, `agent_id`, `metadata`, and all timestamps. Pure read — never
extends TTL. Soft-deleted records report `found: false`.

## brain_reinforce

The **only** TTL renewal: re-arms the full importance TTL after a fetched
memory proved correct and valuable in use. Don't reinforce on sight — fetch,
use, judge, then reinforce.

## brain_forget

Soft delete: excluded from all queries immediately, key TTL shrunk to
`FORGET_GRACE_SECONDS` (default 30 days). An admin can restore within the
grace window (`python src/main.py admin restore <memory_id>`); `admin
hard-delete` is permanent. Harmless outdated memories can simply be left to
expire.

## brain_health

Service status: Redis reachability, active index contract (embedding model,
dim, dtype, metric, index mode), the server-bound `brain_id`, and your
client identity as detected from the MCP handshake. Secret-free.

## brain_audit

Mutation trail for one brain-day (`day` param, `YYYY-MM-DD`, defaults to
today; `limit` max 500, newest first). Events record `action`
(`remember`/`reinforce`/`forget`/`restore`/`hard_delete`), `memory_id`, acting
`agent_id`, and `ts` — never memory text. Audit keys live 90 days
(`AUDIT_RETENTION_DAYS`).

## The recall loop

Search/recent return previews; when a result answers the question, use it
directly; when `has_content` is true and detail is needed, call `brain_get`.
After actually *using* a memory, close the loop: `brain_reinforce` if it
proved correct, `brain_forget` if it proved wrong. Reads alone change nothing.

Memories are claims by past agents, not verified facts — the epistemic
contract for reading and writing them is
[`docs/memory-trust-model.md`](memory-trust-model.md).
