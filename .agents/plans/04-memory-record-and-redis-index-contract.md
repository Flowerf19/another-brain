---
status: approved
approved: 2026-07-11
owner: architecture
created: 2026-07-10
updated: 2026-07-11
scope: step-04
depends_on:
  - .agents/plans/01-architecture-foundation.md
  - .agents/plans/02-directory-and-class-architecture.md
  - .agents/plans/03-model-install-policy.md
---

# Step 04 - Memory Record And Redis Index Contract

This step defines the storage contract: memory fields, Redis key format,
RediSearch index schema, TTL policy, and migration/reindex rules. After this
step is approved, implementation should translate this contract into Python
without further design decisions.

The record model is the **diary model** proven in march7 T2 (schema v3):
one memory = one timeline entry — `timeline_day` + `topic` + short `summary`,
classified by `catalog` (bug, decision, preference, ...), with an optional
`content` field for full detail or a checklist-style breakdown. The store is
**append-only**: there is no merge machinery; same-day repeats are separate
entries (Section 6.6).

This revision deliberately cuts the earlier draft from 35 hash fields / 30
indexed down to **18 hash fields / 14 indexed**. Removed: the translation
pipeline fields (`original_content`, `original_language`,
`canonical_language`), per-record index metadata (`embedding_model`,
`embedding_dim` — they live in `ab:idx:meta`), fields with no producer or no
reader in the MVP (`source_event_ids`, `merge_count`, `expires_at`,
`topic_display`), and speculative fields no query used (`subject_id`,
`observed_at`, `chunk_strategy`, `confidence`, `source`, `memory_model`,
`tags`). Additive fields are cheap to introduce later (Section 5.4); none of
these need to be paid for now.

Soft delete (`deleted_at`) is **kept**: unlike march7's diary (where TTL
drift is enough), this brain is shared by multiple agents — wrong or stale
memories must be actively deletable, with a grace window to recover from a
bad `brain_forget`. Exclusion happens at the **index level** (one mechanism,
resolving the earlier draft's app-layer-vs-index contradiction).

## 1. Memory Record Fields

Every memory is stored as one Redis HASH. The fields below are the complete
contract. The "HASH field" column is the exact Redis HASH key name. The
"Indexed" column says whether RediSearch indexes the field and how.

`memory_id` is NOT a hash field — it is the key suffix, and the repository
derives it from the key on read (as march7 does with `summary_id`).

### 1.1 Identity Fields

| Field | HASH field | Python type | Nullable | Default | Indexed |
| --- | --- | --- | --- | --- | --- |
| `brain_id` | `brain_id` | `str` | no | from config | TAG (required filter) |
| `agent_id` | `agent_id` | `str` | no | from config | not indexed (provenance only) |
| `scope` | `scope` | `str` (enum) | no | tool input | TAG |
| `scope_id` | `scope_id` | `str` | no | tool input | TAG |

### 1.2 Content Fields

| Field | HASH field | Python type | Nullable | Default | Indexed |
| --- | --- | --- | --- | --- | --- |
| `topic` | `topic` | `str` (slug) | no | required | TAG |
| `catalog` | `catalog` | `str` (open tag) | no | `note` | TAG |
| `summary` | `summary` | `str` | no | required | TEXT (BM25, NOSTEM) |
| `content` | `content` | `str` | yes | `""` | TEXT (BM25, NOSTEM) |

Content rules:

- `summary` is the diary line — the short "topic: tóm tắt" text (one or two
  sentences). It is the canonical text: **the embedding is computed from
  `summary`**.
- `content` is optional. Use it when the summary alone is not enough: full
  detail, error output, or a checklist-style breakdown
  (`- [ ] item` / `- [x] item`). It is BM25-searchable but never embedded.
  Capped at `CONTENT_MAX_CHARS` (write-time validation) so a single record
  cannot become a blob dump.
- `topic` is a normalized slug (lowercase-kebab) used for exact filtering,
  and doubles as the display label — there is no separate display field.
- `catalog` is an open vocabulary (validated lowercase-kebab), not a closed
  enum. Starter set: `bug`, `decision`, `preference`, `task`, `fact`, `note`.
  New catalogs need no schema change.

### 1.3 Timeline Fields

| Field | HASH field | Python type | Nullable | Default | Indexed |
| --- | --- | --- | --- | --- | --- |
| `timeline_day` | `timeline_day` | `str` (`YYYY-MM-DD`) | no | derived | TAG |
| `period_start` | `period_start` | `float` (unix ts) | no | `now()` | NUMERIC SORTABLE |
| `period_end` | `period_end` | `float` (unix ts) | no | `= period_start` | NUMERIC SORTABLE |
| `created_at` | `created_at` | `float` (unix ts) | no | `now()` | NUMERIC SORTABLE |
| `updated_at` | `updated_at` | `float` (unix ts) | no | `now()` | not indexed |

Because `period_start`/`period_end` are non-nullable with defaults, plain
range queries work on a fresh index — march7's OR-fallback time clause (for
pre-v3 docs missing `period_end`) is not needed here.

### 1.4 Metadata Fields

| Field | HASH field | Python type | Nullable | Default | Indexed |
| --- | --- | --- | --- | --- | --- |
| `importance` | `importance` | `int` (1-5) | no | `3` | NUMERIC SORTABLE |
| `metadata` | `metadata` | `str` (JSON object) | no | `{}` | not indexed |
| `deleted_at` | `deleted_at` | `float` (unix ts) | yes | absent | NUMERIC (index-level exclusion) |
| `schema_version` | `schema_version` | `int` | no | `1` | not indexed |

`importance` is the anti-bloat lever: it drives the TTL (Section 4) so the
store cannot grow unboundedly, and `brain_search` accepts an optional
`min_importance` filter. Provenance (origin channel, message IDs, host
extras) lives inside `metadata`; a first-class `source_event_ids` field
returns with the observation-ingest phase (additive, Section 5.4). There is
no stored expiry timestamp — the read path derives display expiry from Redis
`EXPIRETIME`, so it can never drift from the real TTL.

### 1.5 Embedding Field

| Field | HASH field | Python type | Nullable | Default | Indexed |
| --- | --- | --- | --- | --- | --- |
| `embedding` | `embedding` | `bytes` (packed FLOAT32) | no | generated | VECTOR HNSW |

Embedding model name and dimension are **index-level** facts, not record
fields. They live in `ab:idx:meta` (Section 2.4) and are enforced by the
startup checks (Section 5.6). A record-level copy would be identical on every
record and was removed.

### 1.6 Enum Values

```
scope:    user | project | global
catalog:  open vocabulary; starter set: bug | decision | preference | task | fact | note
```

Scope notes:

- `channel` (from march7) is deliberately **not** a scope. The unify
  principle (architecture doc: one shared store, no default partition by
  `agent_id`) applies equally to conversations — knowledge learned in one
  channel must be recallable from another. Where the memory came from is
  provenance (`metadata`), not a retrieval partition.
  march7 only partitioned by channel in T1 (short-term context); its
  long-term T2 filtered by `user_id` alone.
- `scope=global` uses the literal `scope_id="global"`, enforced by tool
  input validation — otherwise identical global memories scatter across
  arbitrary scope_ids and never find each other in search.
- Adding a scope value later is additive (TAG field, app-level enum): no
  schema change.

### 1.7 Serialization Rules

- All timestamps are Unix epoch floats (seconds, sub-second precision).
- `timeline_day` is a `YYYY-MM-DD` string derived from `period_start` in the
  configured timezone (`TIMELINE_TIMEZONE`).
- `metadata` is a JSON-encoded string; the repository layer
  serializes/deserializes.
- `embedding` is raw packed bytes: `struct.pack(f"{len(v)}f", *v)`
  (equivalent to `numpy.float32(...).tobytes()`), as in march7
  `diary/codec.py`.
- TAG values used in queries must be escaped with the march7
  `escape_tag_value` rule (backslash-escape every non-word char) — `-` in
  `timeline_day` breaks unescaped TAG queries.
- Retention is enforced by Redis key TTL (`EXPIRE`), which the repository
  sets and refreshes. Display expiry is derived from `EXPIRETIME` at read
  time — there is no stored copy to drift out of sync.
- Soft delete: `deleted_at` is **absent** on live records. `brain_forget`
  sets it to `now()` and shrinks the key TTL to `FORGET_GRACE_SECONDS`
  (never extends a shorter remaining TTL). Every search/recent query
  excludes soft-deleted records at the index level with
  `(-@deleted_at:[-inf +inf])` — a record missing the field matches the
  negation; a record having it is excluded. No app-layer double filtering.
- Within the grace window a record can be restored by clearing `deleted_at`
  (`HDEL`) and re-applying the importance TTL. Hard delete (`DEL`) is
  admin-only. Both directions write audit events.

## 2. Redis Key Format

All keys use the `ab:` prefix agreed in Step 02. The type segment comes
**before** `brain_id` so each key family has a fixed literal prefix — the
earlier draft (`ab:{brain_id}:memory:{id}`) forced the index prefix to `ab:`,
which would have indexed audit and meta HASHes into search results.

### 2.1 Memory Keys

```
ab:memory:{brain_id}:{memory_id}
```

- `brain_id`: the isolation namespace, e.g. `flowerf-main`.
- `memory_id`: UUID string (derived back from the key on read).
- Type: HASH.
- TTL: set from importance (Section 4), re-armed only by `brain_reinforce`.

### 2.2 Audit Keys

```
ab:audit:{brain_id}:{YYYY-MM-DD}
```

- One HASH per brain per day; audit events keyed by event ID.
- TTL: 90 days (`AUDIT_RETENTION_DAYS`).
- Not matched by the index prefix (`ab:memory:`), so never searchable.

### 2.3 Index Key

```
ab:idx:memory
```

- One global RediSearch index, `PREFIX 1 ab:memory:`, over all brains.
- `brain_id` is a required filter in every query. Never query without it.
- Alternative (postponed): one index per brain if scale requires it.

### 2.4 Index Metadata Key

```
ab:idx:meta
```

- Type: HASH.
- Stores index version, embedding model name, embedding dimension, vector
  dtype, and distance metric. This is the **only** place embedding
  model/dimension are recorded.
- Used by startup checks and migration logic.

## 3. RediSearch Index Schema

### 3.1 Index Creation Command

```redis
FT.CREATE ab:idx:memory
  ON HASH
  PREFIX 1 ab:memory:
  SCHEMA
    brain_id       TAG
    scope          TAG
    scope_id       TAG
    topic          TAG
    catalog        TAG
    timeline_day   TAG
    summary        TEXT NOSTEM
    content        TEXT NOSTEM
    importance     NUMERIC SORTABLE
    period_start   NUMERIC SORTABLE
    period_end     NUMERIC SORTABLE
    created_at     NUMERIC SORTABLE
    deleted_at     NUMERIC
    embedding      VECTOR HNSW 6
      TYPE FLOAT32
      DIM 640
      DISTANCE_METRIC COSINE
```

### 3.2 Field Design Rationale

**TEXT fields use `NOSTEM`:** the service is multilingual; English stemming
is wrong for Vietnamese and mixed-language content. `NOSTEM` keeps BM25 as
pure token matching. Per-language analyzers can be added later as an explicit
migration.

`brain_id`, `scope`, `scope_id`, and the soft-delete exclusion are always
present; optional `@topic:{...}`, `@catalog:{...}`, `@timeline_day:{...}`
(day tracing), `@importance:[<min> +inf]` (min_importance filter), and
`@period_start`/`@period_end` range clauses — exactly the filters the query
contract uses. Nothing else is TAG'd.

**NUMERIC SORTABLE mirrors march7 v3** (`importance`, `created_at`,
`period_start`, `period_end`). `brain_recent` sorts by `period_start`;
`importance` backs the optional `min_importance` search filter. `updated_at`
is stored but not indexed — no query sorts or filters by it.

**VECTOR HNSW:** `M=6`, `TYPE FLOAT32`, `DIM 640` (Harrier default),
`DISTANCE_METRIC COSINE`. `DIM` must match the active embedding model;
changing it requires a full reindex (Section 5). `FLAT` is acceptable for
correctness testing; HNSW is the default.

**Soft-delete exclusion is index-level:** `deleted_at` is NUMERIC and absent
on live records; queries exclude deleted records with
`(-@deleted_at:[-inf +inf])` (DIALECT 2). One mechanism, no app-layer
double filtering.

### 3.3 Index Existence Check

At startup, `RedisIndexManager`:

1. Checks `ab:idx:memory` via `FT.INFO`.
2. If it exists, compares the indexed vector `DIM` against the configured
   embedding dimension (parse `FT.INFO` best-effort, as march7
   `diary/schema.py` does for RESP2/RESP3 shapes).
3. On mismatch, refuses to start with a migration-required error.
4. If the index does not exist, creates it (tolerating a concurrent
   "already exists" reply).
5. Writes/updates `ab:idx:meta`.

## 4. TTL And Retention Policy

### 4.1 Importance To TTL Mapping

Inherit the proven march7 T2 table:

| Importance | TTL (seconds) | TTL (human) |
| ---: | ---: | --- |
| 5 | 31,536,000 | 365 days |
| 4 | 15,552,000 | 180 days |
| 3 | 7,776,000 | 90 days |
| 2 | 2,592,000 | 30 days |
| 1 | 604,800 | 7 days |

### 4.2 TTL Application Rules

1. On `brain_remember`: `EXPIRE` from the importance TTL.
2. On `brain_reinforce(memory_id)` — the **only** renewal mechanism:
   re-apply the full importance TTL, bump `updated_at`, write an audit
   event. This is an explicit LLM decision made *after* fetching and using
   a memory and judging it still correct and valuable. Renewal is never a
   code side effect: no read path refreshes TTL automatically, because code
   cannot know whether a memory is right — only the model reading it can.
   The counterpart decision is `brain_forget`: a fetched memory that turned
   out wrong gets forgotten on the spot, not left to ride its TTL.
3. On `brain_forget` (soft delete): set `deleted_at = now()`, shrink TTL to
   `FORGET_GRACE_SECONDS` (only if shorter than the remaining TTL), write an
   audit event. The record stays recoverable until the grace TTL expires.
4. On restore (admin, within grace): `HDEL deleted_at`, re-apply the
   importance TTL, write an audit event.
5. Hard delete (admin-only): `DEL` + audit event.
6. On `brain_search` / `brain_recent` / `brain_get`: never refresh TTL —
   all reads are pure. Appearing in top-K is not use, and even a by-ID
   fetch is not yet a judgment that the memory is correct.
7. Display expiry is derived from `EXPIRETIME` at read time; there is no
   stored expiry field.

Failure direction: if the LLM never reinforces, everything expires at its
baseline importance TTL — the system fails toward forgetting, never toward
bloat. A wrong memory that keeps getting fetched is never auto-renewed; it
is either forgotten explicitly or dies on schedule.

### 4.3 Configurable TTL

```text
TTL_IMPORTANCE_5=31536000
TTL_IMPORTANCE_4=15552000
TTL_IMPORTANCE_3=7776000
TTL_IMPORTANCE_2=2592000
TTL_IMPORTANCE_1=604800
```

If any override is set, all five must be set; partial overrides are rejected
at config validation.

### 4.4 Audit TTL

```text
AUDIT_RETENTION_DAYS=90
```

## 5. Migration And Reindex Rules

### 5.1 Schema Versioning

- `schema_version` on each HASH tracks the record-level layout version.
  Current: `1`. Not indexed — migrations scan keys, they don't search.
- `ab:idx:meta` tracks the index-level version separately.

### 5.2 Embedding Dimension Change

Changing the embedding model dimension (e.g., Harrier 640 → Qwen3 1024)
requires a full reindex:

1. Create a new index with the new `DIM` (e.g., `ab:idx:memory_v2`).
2. Re-embed all `summary` fields with the new model.
3. Replace `embedding` on each HASH.
4. Drop the old index, repoint, update `ab:idx:meta`.

Explicit, admin-triggered. The server refuses to start if the configured
`EMBEDDING_DIM` does not match the existing index dimension.

### 5.3 Vector Dtype Change

Also a full reindex. MVP: only `FLOAT32` (per Step 03).

### 5.4 Schema Version Bump (Field Changes)

Adding an indexed field: prefer `FT.ALTER ... SCHEMA ADD` when available
(march7 used this for the v2→v3 diary fields), falling back to drop-and-
recreate. Existing HASHes lacking the field simply don't match filters on it
— acceptable for additive fields. Removing or renaming a field is breaking:
migration script per HASH, bump `schema_version`, drop and recreate the
index.

### 5.5 Index Recreate Procedure

```redis
FT.DROPINDEX ab:idx:memory
FT.CREATE ab:idx:memory ... (new schema)
```

RediSearch re-indexes all matching HASHes automatically after recreate.

### 5.6 Startup Safety Checks

1. Redis Stack reachable and `FT.SEARCH` module present.
2. `ab:idx:memory` exists or is created.
3. Indexed vector `DIM` matches `EMBEDDING_DIM` from config.
4. `ab:idx:meta` matches the active index state.
5. Any failure: clear error, refuse to start. Never silently create a
   mismatched index.

## 6. Search Query Contract

All queries inherit the march7 T2 shapes (`diary/store.py`), which are
production-proven, including TAG escaping and RESP2/RESP3 result parsing.

### 6.1 Vector KNN Search

```redis
FT.SEARCH ab:idx:memory
  "(@brain_id:{flowerf\-main} @scope:{user} @scope_id:{flowerf}
    (-@deleted_at:[-inf +inf]))=>[KNN 20 @embedding $vec AS score]"
  PARAMS 2 vec <packed_float32_bytes>
  SORTBY score ASC
  LIMIT 0 20
  DIALECT 2
```

- `brain_id`, `scope`, `scope_id`, and the soft-delete exclusion are always
  present; optional `@topic:{...}`, `@catalog:{...}`,
  `@importance:[<min> +inf]` (min_importance filter), and
  `@period_start`/`@period_end` range clauses.
- Raw hits are gated in the application by cosine floor
  (`SEARCH_MIN_COSINE`), as march7 gates with `T2_MIN_COSINE`.

### 6.2 BM25 Search

```redis
FT.SEARCH ab:idx:memory
  "@brain_id:{flowerf\-main} @scope:{user} @scope_id:{flowerf}
   (-@deleted_at:[-inf +inf]) @summary|content:(query terms)"
  SORTBY _score DESC
  LIMIT 0 20
  DIALECT 2
```

BM25 is **ranking-only**: BM25-only docs are still gated by the cosine floor
computed in Python from their stored embedding (inherit march7 fix B3 — BM25
must not bypass the similarity gate).

### 6.3 Recent / Timeline Query

```redis
FT.SEARCH ab:idx:memory
  "@brain_id:{flowerf\-main} @scope:{user} @scope_id:{flowerf}
   (-@deleted_at:[-inf +inf]) @period_start:[1704067200 1706745599]"
  SORTBY period_start DESC
  LIMIT 0 20
  DIALECT 2
```

Pure filter + sort. Used by `brain_recent`.

### 6.4 Rank Fusion

`MemorySearchEngine` runs KNN and BM25, then fuses (march7 order preserved):

1. Top-K from each (default K=20).
2. Reciprocal rank fusion: `score = 1/(60 + rank_knn) + 1/(60 + rank_bm25)`,
   fused **without truncation**.
3. Apply the cosine gate (KNN-gated ids stripped; BM25-only docs gated in
   Python), then apply the final limit — gating after truncation
   under-returns.
4. Deduplicate by `memory_id`; return `relevance_score` and `score_source`
   (knn, bm25, fused).

### 6.5 Search Result Payload

`brain_search` and `brain_recent` return **summaries inline, detail on
demand**:

- Each result carries `memory_id`, `topic`, `catalog`, `summary`,
  `timeline_day`, `importance`, `has_content` (computed bool), and
  `relevance_score` / `score_source`. This payload is small by design —
  `summary` is the 1–2 sentence diary line.
- `content` (detail / checklist) is **not** returned by search. When the
  LLM needs it, it calls `brain_get(memory_id)` — a pure read. After using
  the memory, the agent closes the loop explicitly: `brain_reinforce` if it
  proved correct and valuable (re-arms TTL, Section 4.2), or `brain_forget`
  if it proved wrong.
- `embedding` is never returned to the host.

Topic-only previews were rejected: a slug carries too little signal for the
LLM to decide what to fetch, so it fetches everything or nothing. The
summary line is the right preview granularity — it answers most questions by
itself and makes `brain_get` a genuine detail pull, not a mandatory second
hop.

### 6.6 No Merge — Append-Only Store

There is no merge machinery. `brain_remember` always appends a new record;
same-day repeats of the same knowledge are separate entries. This is
deliberate:

- Intra-day duplicates are cheap (TTL cleans them) and are themselves a
  trace — "this came up three times today" is visible history, filterable
  by `@timeline_day` + `@topic`.
- Merge was the most complex part of march7's write path (re-embed on
  merge, cosine-floor tuning, summary rewriting) and arrived there only as
  the P2.x patch series with its own bug trail; march7 shipped append-only
  first (`embedding_service=None` mode).
- Renewal — merge's other job — is handled by explicit `brain_reinforce`
  (Section 4.2).

If intra-day duplication ever becomes a real problem, merge can return as a
purely additive feature: the schema already carries everything it needs
(`timeline_day`, `period_start`, `period_end`, `embedding`).

## 7. Config Values Added By This Step

```text
# Redis connection
REDIS_URL=redis://localhost:6379
REDIS_KEY_PREFIX=ab

# Index
REDIS_INDEX_NAME=ab:idx:memory
REDIS_VECTOR_DTYPE=FLOAT32
REDIS_DISTANCE_METRIC=COSINE
REDIS_VECTOR_INDEX_MODE=HNSW
REDIS_HNSW_M=6

# TTL
TTL_IMPORTANCE_5=31536000
TTL_IMPORTANCE_4=15552000
TTL_IMPORTANCE_3=7776000
TTL_IMPORTANCE_2=2592000
TTL_IMPORTANCE_1=604800

# Audit
AUDIT_RETENTION_DAYS=90

# Search
SEARCH_TOP_K=20
SEARCH_FUSION_K=60
SEARCH_MIN_COSINE=0.30

# Validation
CONTENT_MAX_CHARS=4000

# Forget (soft-delete grace window)
FORGET_GRACE_SECONDS=2592000

# Timezone for timeline_day derivation
TIMELINE_TIMEZONE=Asia/Ho_Chi_Minh

# Schema
SCHEMA_VERSION=1
```

## 8. Classes Updated By This Step

- `RedisKeyBuilder` — key formats in Section 2.
- `RedisIndexManager` — index creation, verification, startup checks
  (Sections 3.3, 5.6).
- `RedisMemoryRepository` — HASH read/write, TTL, soft delete/restore/hard
  delete, reinforce re-arm, queries in Section 6.
- `RedisMemoryMapper` — serialization rules in Section 1.7 (including TAG
  escaping and RESP2/RESP3 result parsing per march7 `diary/codec.py`).
- `RetentionPolicy` — TTL mapping in Section 4.
- `MigrationRunner` — migration/reindex procedures in Section 5.
- `MemorySearchEngine` — rank fusion and cosine gating in Section 6.4.

## Review Decisions

Approve or change these before implementation begins:

1. Diary record model: `timeline_day` + `topic` + `summary` (embedded),
   `catalog` open-vocabulary classification, optional `content` for
   detail/checklist. 18 hash fields, 14 indexed.
2. Key format is `ab:memory:{brain_id}:{memory_id}` so the index prefix
   `ab:memory:` cannot match audit/meta keys.
3. One global index with `brain_id` as required filter.
4. Soft delete via `deleted_at`: `brain_forget` sets it and shrinks TTL to
   `FORGET_GRACE_SECONDS` (30-day default recovery window); exclusion is
   index-level only (`(-@deleted_at:[-inf +inf])`, no app-layer double
   filter); restore and hard delete are admin-only, all audited.
5. No translation-pipeline fields in MVP (`NOSTEM` covers multilingual
   BM25); additive migration later if needed.
6. Embedding model/dimension recorded only in `ab:idx:meta`, never
   per-record; server refuses to start on dimension mismatch.
7. TEXT fields (`summary`, `content`) use `NOSTEM`.
8. Embedding is computed from `summary` only; `content` is BM25-searchable
   but never embedded.
9. TTL table inherits the proven march7 values (365/180/90/30/7 days).
   Renewal happens **only** via explicit `brain_reinforce` — never on any
   read, never automatically by code. Failure direction is forgetting, not
   bloat.
10. Search inherits march7 shapes: `=>[KNN ...]`, app-layer cosine gate,
    RRF with K=60, gate-before-limit, BM25 ranking-only.
11. No merge — the store is append-only. Same-day duplicates are accepted
    as usage traces (filterable by `timeline_day` + `topic`) and TTL cleans
    them up. Merge may return later as a purely additive feature.
12. `timeline_day` derived in `TIMELINE_TIMEZONE`, not UTC.
13. `schema_version=1`, stored but not indexed.
14. Scope enum is `user | project | global` — no `channel` scope. Memory is
    unified across conversations; conversation origin is provenance
    (`metadata`) only. `scope=global` pins `scope_id="global"`.
15. Field audit (YAGNI pass): no `topic_display` (`topic` doubles as the
    label), no `expires_at` (derived from `EXPIRETIME`), no
    `source_event_ids` / `merge_count` in MVP (provenance in `metadata`;
    first-class field returns with observation ingest). `updated_at` is
    stored but not indexed. `importance` stays NUMERIC SORTABLE and backs
    the `min_importance` search filter — it is the anti-bloat lever via TTL.
16. Search returns summaries inline (`memory_id`, `topic`, `catalog`,
    `summary`, `timeline_day`, `importance`, `has_content`; no `content`,
    never `embedding`). `brain_get(memory_id)` pulls the full record as a
    pure read. After use, the agent closes the loop explicitly:
    `brain_reinforce` (correct and valuable → re-arm TTL) or `brain_forget`
    (wrong → soft delete). The tool surface gains `brain_reinforce`.

## Next Slice After Approval

Implementation, in the MVP milestone order from the architecture doc:

1. `src/memory/models.py` — dataclasses, enums, `MemoryRecord`.
2. `src/storage/redis_keys.py` — `RedisKeyBuilder`.
3. `src/storage/redis_index.py` — `RedisIndexManager`.
4. `src/storage/redis_repository.py` — repository + mapper.
5. `src/config.py` — `AppConfig` with all config values.
6. `src/memory/service.py` — `MemoryService`.
7. `src/mcp/tools.py` — MCP tool surface.
8. `docker/docker-compose.yml` — Redis Stack + service.
9. Tests.
