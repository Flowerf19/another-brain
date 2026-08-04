# MCP tools

Server name: `another-brain`. `brain_id` comes from process configuration and
`agent_id` comes from the MCP client handshake; neither is a tool parameter.

`scope` is `user`, `project`, or `global`. User and project memories require a
`scope_id`; global scope normalizes it to `global`.

## `brain_remember`

Appends one diary entry. Required fields are `topic`, `summary`, and `scope`.
Optional fields are `scope_id`, `catalog`, `content`, `importance`, and
`metadata`. Importance 5..1 maps to 365/180/90/30/7 days. The embedding uses
only the humanized topic and summary; content is FTS5-only.

Returns `memory_id`, `timeline_day`, and `expires_at`.

## `brain_search`

Hybrid lexical and vector search. Required fields are `query` and `scope`.
Optional filters are `scope_id`, `topic`, `catalog`, `timeline_day`,
`min_importance`, and `days`.

Returns previews with scores, never full content or embeddings. Lexical-only
matches do not have to pass the vector cosine floor.

## `brain_recent`

Returns newest live previews for a scope without embedding a query and without
renewing retention. Parameters are `scope`, optional `scope_id`, `days`, and
`limit`.

## `brain_get`

Returns one live memory in full by `memory_id`, including content, metadata,
identity provenance, and timestamps. It is a pure read.

## `brain_reinforce`

Re-arms the importance-derived TTL after the memory has actually proved useful.
This is the only ordinary retention renewal.

## `brain_forget`

Soft-deletes a wrong or stale memory immediately. An administrator can restore
it during the grace period with `another-brain admin restore <memory-id>` or
remove it permanently with `another-brain admin hard-delete <memory-id>`.

## `brain_health`

Reports secret-free SQLite, model-installation, embedding, and client status.
It does not load the model merely to answer health.

## `brain_audit`

Returns newest mutation events for an optional timeline day. Events contain
action, memory id, acting agent id, and time; memory text is never included.

## Recall loop

Search or inspect recent previews, fetch full detail only when needed, then
reinforce after successful use or forget when disproved. Memories are claims
from previous agents rather than verified facts; see the
[trust model](memory-trust-model.md).
