"""SQLite schema v1."""

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS embedding_profiles (
    profile_id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    revision TEXT NOT NULL,
    variant TEXT NOT NULL,
    dimension INTEGER NOT NULL CHECK (dimension = 640),
    dtype TEXT NOT NULL CHECK (dtype = 'float32-le'),
    normalized INTEGER NOT NULL CHECK (normalized = 1),
    input_version INTEGER NOT NULL CHECK (input_version = 2),
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    row_id INTEGER PRIMARY KEY,
    memory_id TEXT NOT NULL,
    brain_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('user','project','global')),
    scope_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    catalog TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    timeline_day TEXT NOT NULL,
    period_start INTEGER NOT NULL,
    period_end INTEGER NOT NULL CHECK (period_end >= period_start),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL CHECK (updated_at >= created_at),
    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
    expires_at INTEGER NOT NULL,
    deleted_at INTEGER,
    metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
    profile_id TEXT NOT NULL REFERENCES embedding_profiles(profile_id),
    embedding BLOB NOT NULL CHECK (length(embedding) = 2560),
    record_version INTEGER NOT NULL CHECK (record_version > 0),
    UNIQUE (brain_id, memory_id),
    CHECK ((scope = 'global' AND scope_id = 'global') OR scope != 'global')
);

CREATE INDEX IF NOT EXISTS idx_memories_recent
ON memories(brain_id, scope, scope_id, deleted_at, expires_at, created_at DESC, memory_id);
CREATE INDEX IF NOT EXISTS idx_memories_expiry ON memories(expires_at);
CREATE INDEX IF NOT EXISTS idx_memories_topic
ON memories(brain_id, scope, scope_id, topic, deleted_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_memories_catalog
ON memories(brain_id, scope, scope_id, catalog, deleted_at, expires_at);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    topic, summary, content,
    content='memories', content_rowid='row_id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memory_fts(rowid, topic, summary, content)
    VALUES (new.row_id, new.topic, new.summary, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, topic, summary, content)
    VALUES ('delete', old.row_id, old.topic, old.summary, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF topic,summary,content ON memories BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, topic, summary, content)
    VALUES ('delete', old.row_id, old.topic, old.summary, old.content);
    INSERT INTO memory_fts(rowid, topic, summary, content)
    VALUES (new.row_id, new.topic, new.summary, new.content);
END;

CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN ('remember','reinforce','forget','restore','hard_delete')
    ),
    event_at INTEGER NOT NULL,
    timeline_day TEXT NOT NULL,
    detail_json TEXT NOT NULL CHECK (json_valid(detail_json))
);
CREATE INDEX IF NOT EXISTS idx_audit_day
ON audit_events(brain_id, timeline_day, event_at DESC, event_id ASC);
"""
