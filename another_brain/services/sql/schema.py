"""Schema v1 — exactly six tables (TASK-048).

``memories`` is the only business table; the other five are system support:

1. ``schema_migrations``   — checksummed migration ledger (TASK-049);
2. ``embedding_profiles``  — locked embedding contract, FK target of memories;
3. ``memories``            — the diary rows (identity, text, timeline,
                             metadata JSON, embedding BLOB);
4. ``memory_fts``          — FTS5 external-content index over memories,
                             synchronized by triggers (never a source of truth);
5. ``import_runs``         — durable JSONL import checkpoints;
6. ``audit_events``        — structural mutation facts, deliberately no
                             memory FK so hard-delete preserves history.

Timestamps are signed INTEGER epoch milliseconds (negative values are legal
per contract). ``metadata`` and audit ``detail_json`` are stored as canonical
JSON text and enforced as JSON objects at the schema level. Identity and
text fields are non-empty at the schema level (``length(...) > 0``). The
embedding BLOB is exactly 2560 bytes (640 × float32, little-endian).

The DDL is immutable and split into single statements
(``DDL_V1_STATEMENTS``) so the migration runner can execute each inside one
exclusive transaction (``executescript`` would commit implicitly).
``DDL_V1`` and :func:`checksum` fingerprint the exact text for TASK-049.

v1 is unreleased, so this DDL is revised directly. A store built from an
older draft is rejected by the migration ledger rather than treated as
compatible with the released schema contract.
"""
from __future__ import annotations

import hashlib

SCHEMA_VERSION = 1

#: The six v1 tables, minus FTS5 shadow tables; used by
#: :meth:`~another_brain.services.sql.connection.SQLiteConnectionFactory.verify_schema`.
SCHEMA_TABLES: frozenset[str] = frozenset({
    "schema_migrations",
    "embedding_profiles",
    "memories",
    "memory_fts",
    "import_runs",
    "audit_events",
})

DDL_V1_STATEMENTS: list[str] = [
    """CREATE TABLE schema_migrations(
  version INTEGER PRIMARY KEY,
  checksum TEXT NOT NULL,
  applied_at INTEGER NOT NULL
);""",
    """CREATE TABLE embedding_profiles(
  profile_id TEXT PRIMARY KEY,
  model_repo TEXT NOT NULL,
  model_revision TEXT NOT NULL,
  variant TEXT NOT NULL,
  dimension INTEGER NOT NULL CHECK(dimension > 0),
  dtype TEXT NOT NULL,
  normalized INTEGER NOT NULL CHECK(normalized IN (0, 1)),
  tokenizer_sha256 TEXT NOT NULL,
  config_sha256 TEXT NOT NULL,
  prompt_utf8_sha256 TEXT NOT NULL,
  query_prompt TEXT NOT NULL,
  input_version INTEGER NOT NULL CHECK(input_version > 0),
  created_at_ms INTEGER NOT NULL
);""",
    """CREATE TABLE memories(
  row_id INTEGER PRIMARY KEY,
  memory_id TEXT NOT NULL,
  brain_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  topic TEXT NOT NULL,
  catalog TEXT NOT NULL,
  summary TEXT NOT NULL,
  content TEXT NOT NULL,
  timeline_day TEXT NOT NULL,
  period_start_ms INTEGER,
  period_end_ms INTEGER,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  importance INTEGER NOT NULL CHECK(importance BETWEEN 1 AND 5),
  expires_at_ms INTEGER NOT NULL,
  deleted_at_ms INTEGER,
  metadata TEXT NOT NULL CHECK(json_valid(metadata) AND json_type(metadata) = 'object'),
  profile_id TEXT NOT NULL REFERENCES embedding_profiles(profile_id),
  embedding BLOB NOT NULL CHECK(length(embedding) = 2560),
  record_version INTEGER NOT NULL CHECK(record_version > 0),
  UNIQUE(brain_id, memory_id),
  CHECK(length(memory_id) > 0),
  CHECK(length(brain_id) > 0),
  CHECK(length(agent_id) > 0),
  CHECK(length(topic) > 0),
  CHECK(length(catalog) > 0),
  CHECK(length(summary) > 0),
  CHECK(updated_at_ms >= created_at_ms),
  CHECK(
    period_start_ms IS NULL OR period_end_ms IS NULL
    OR period_start_ms <= period_end_ms
  )
);""",
    """CREATE VIRTUAL TABLE memory_fts USING fts5(
  topic, summary, content,
  content='memories', content_rowid='row_id',
  tokenize='unicode61 remove_diacritics 2'
);""",
    """CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memory_fts(rowid, topic, summary, content)
  VALUES (new.row_id, new.topic, new.summary, new.content);
END;""",
    """CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, topic, summary, content)
  VALUES ('delete', old.row_id, old.topic, old.summary, old.content);
END;""",
    """CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, topic, summary, content)
  VALUES ('delete', old.row_id, old.topic, old.summary, old.content);
  INSERT INTO memory_fts(rowid, topic, summary, content)
  VALUES (new.row_id, new.topic, new.summary, new.content);
END;""",
    """CREATE TABLE import_runs(
  export_id TEXT PRIMARY KEY,
  artifact_sha256 TEXT NOT NULL UNIQUE,
  format_version INTEGER NOT NULL CHECK(format_version > 0),
  status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
  last_committed_seq INTEGER NOT NULL CHECK(last_committed_seq >= 0),
  imported_count INTEGER NOT NULL CHECK(imported_count >= 0),
  skipped_count INTEGER NOT NULL CHECK(skipped_count >= 0),
  failed_count INTEGER NOT NULL CHECK(failed_count >= 0),
  started_at_ms INTEGER NOT NULL,
  completed_at_ms INTEGER
);""",
    """CREATE TABLE audit_events(
  event_id TEXT PRIMARY KEY,
  brain_id TEXT NOT NULL,
  memory_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  action TEXT NOT NULL
    CHECK(action IN ('remember', 'reinforce', 'forget', 'restore', 'hard_delete')),
  event_at_ms INTEGER NOT NULL,
  timeline_day TEXT NOT NULL,
  detail_json TEXT NOT NULL
    CHECK(json_valid(detail_json) AND json_type(detail_json) = 'object')
);""",
    """CREATE INDEX memories_recent
  ON memories(brain_id, deleted_at_ms, expires_at_ms,
              created_at_ms DESC, memory_id ASC);""",
    """CREATE INDEX memories_topic
  ON memories(brain_id, topic, deleted_at_ms, expires_at_ms,
              created_at_ms DESC, memory_id ASC);""",
    """CREATE INDEX memories_catalog
  ON memories(brain_id, catalog, deleted_at_ms, expires_at_ms,
              created_at_ms DESC, memory_id ASC);""",
    """CREATE INDEX memories_expiry ON memories(expires_at_ms);""",
    """CREATE INDEX memories_deleted ON memories(deleted_at_ms);""",
    """CREATE INDEX audit_day
  ON audit_events(brain_id, timeline_day, event_at_ms DESC, event_id ASC);""",
]

DDL_V1 = "\n".join(DDL_V1_STATEMENTS) + "\n"


def checksum() -> str:
    """SHA-256 of the immutable v1 DDL (the exact string executed)."""
    return hashlib.sha256(DDL_V1.encode("utf-8")).hexdigest()
