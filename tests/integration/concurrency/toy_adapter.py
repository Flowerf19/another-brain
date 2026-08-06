"""Toy SQLite store for validating the concurrency harness (TASK-007).

Deliberately mirrors the locked production patterns — WAL, busy_timeout,
cross-process schema lock, BEGIN IMMEDIATE with bounded busy retry, FTS5
external content, expired/deleted live filtering — at toy scale, so harness
bugs surface here before TASK-055 runs against the real repository.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from filelock import FileLock

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000
RETRY_ATTEMPTS = 5
RETRY_BASE_S = 0.05

DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS memories(
  row_id INTEGER PRIMARY KEY,
  memory_id TEXT NOT NULL,
  brain_id TEXT NOT NULL,
  summary TEXT NOT NULL,
  importance INTEGER NOT NULL,
  created_at_ms INTEGER NOT NULL,
  expires_at_ms INTEGER NOT NULL,
  deleted_at_ms INTEGER,
  UNIQUE(brain_id, memory_id)
);
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  summary, content='memories', content_rowid='row_id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memory_fts(rowid, summary) VALUES (new.row_id, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, summary) VALUES ('delete', old.row_id, old.summary);
END;
"""

NOW_MS = 1_785_000_000_000
TTL_MS = 30 * 86_400_000
GRACE_MS = 30 * 86_400_000


class BusyExhausted(Exception):
    """Typed bounded error after the busy-retry envelope is exhausted."""


class Store:
    def __init__(self, con: sqlite3.Connection, *, retry_attempts: int = RETRY_ATTEMPTS,
                 retry_base_s: float = RETRY_BASE_S):
        self.con = con
        self.retry_attempts = retry_attempts
        self.retry_base_s = retry_base_s


def _connect(db_path: Path, busy_timeout_s: float) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=busy_timeout_s)
    con.execute(f"PRAGMA busy_timeout={int(busy_timeout_s * 1000)}")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def open_store(db_path: Path, *, busy_timeout_s: float = BUSY_TIMEOUT_MS / 1000.0,
               retry_attempts: int = RETRY_ATTEMPTS,
               retry_base_s: float = RETRY_BASE_S) -> Store:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # cross-process schema lock: exactly one creator runs migrations
    with FileLock(str(db_path) + ".schema.lock"):
        con = _connect(db_path, busy_timeout_s)
        con.execute("PRAGMA journal_mode=WAL")
        version = con.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0] if con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone() else 0
        if version < SCHEMA_VERSION:
            con.executescript(DDL)
            con.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, int(time.time() * 1000)),
            )
            con.commit()
    return Store(con, retry_attempts=retry_attempts, retry_base_s=retry_base_s)


def close_store(store: Store) -> None:
    store.con.close()


def _busy_retry(store: Store, fn):
    last = None
    for attempt in range(store.retry_attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            last = exc
            time.sleep(store.retry_base_s * (2 ** attempt) + attempt * 0.01)
    raise BusyExhausted(f"busy after {store.retry_attempts} attempts: {last}")


def apply(store: Store, op: str, memory_id: str, rng) -> str:
    con = store.con
    if op == "remember":
        def insert():
            con.execute("BEGIN IMMEDIATE")
            try:
                con.execute(
                    "INSERT OR IGNORE INTO memories(memory_id, brain_id, summary,"
                    " importance, created_at_ms, expires_at_ms) VALUES (?,?,?,?,?,?)",
                    (memory_id, "toy-brain", f"payload {memory_id}", 3, NOW_MS, NOW_MS + TTL_MS),
                )
                con.commit()
            except Exception:
                con.rollback()
                raise
        _busy_retry(store, insert)
        return "remembered"
    if op == "reinforce":
        def reinforce():
            con.execute("BEGIN IMMEDIATE")
            try:
                cur = con.execute(
                    "UPDATE memories SET expires_at_ms=? WHERE memory_id=?"
                    " AND deleted_at_ms IS NULL AND expires_at_ms > ?",
                    (NOW_MS + TTL_MS, memory_id, NOW_MS),
                )
                con.commit()
                return cur.rowcount
            except Exception:
                con.rollback()
                raise
        return "reinforced" if _busy_retry(store, reinforce) else "not_found"
    if op == "forget":
        def forget():
            con.execute("BEGIN IMMEDIATE")
            try:
                cur = con.execute(
                    "UPDATE memories SET deleted_at_ms=?,"
                    " expires_at_ms=MIN(expires_at_ms, ?) WHERE memory_id=?"
                    " AND deleted_at_ms IS NULL AND expires_at_ms > ?",
                    (NOW_MS, NOW_MS + GRACE_MS, memory_id, NOW_MS),
                )
                con.commit()
                return cur.rowcount
            except Exception:
                con.rollback()
                raise
        return "forgotten" if _busy_retry(store, forget) else "not_found"
    if op == "restore":
        def restore():
            con.execute("BEGIN IMMEDIATE")
            try:
                cur = con.execute(
                    "UPDATE memories SET deleted_at_ms=NULL, expires_at_ms=?"
                    " WHERE memory_id=? AND deleted_at_ms IS NOT NULL AND expires_at_ms > ?",
                    (NOW_MS + TTL_MS, memory_id, NOW_MS),
                )
                con.commit()
                return cur.rowcount
            except Exception:
                con.rollback()
                raise
        return "restored" if _busy_retry(store, restore) else "not_found"
    if op == "get":
        row = con.execute(
            "SELECT summary FROM memories WHERE memory_id=?"
            " AND deleted_at_ms IS NULL AND expires_at_ms > ?",
            (memory_id, NOW_MS),
        ).fetchone()
        return "hit" if row else "not_found"
    if op == "recent":
        con.execute(
            "SELECT memory_id FROM memories WHERE deleted_at_ms IS NULL"
            " AND expires_at_ms > ? ORDER BY created_at_ms DESC, memory_id ASC LIMIT 5",
            (NOW_MS,),
        ).fetchall()
        return "listed"
    if op == "search":
        con.execute(
            "SELECT m.memory_id FROM memory_fts f JOIN memories m ON m.row_id = f.rowid"
            " WHERE memory_fts MATCH ? AND m.deleted_at_ms IS NULL AND m.expires_at_ms > ?"
            " ORDER BY bm25(memory_fts) LIMIT 5",
            ('"payload"', NOW_MS),
        ).fetchall()
        return "searched"
    raise ValueError(f"unknown op {op}")


def preseed(db_path: Path, count: int, hot_ids: int) -> None:
    store = open_store(db_path)
    for i in range(1, hot_ids + 1):
        apply(store, "remember", f"hot-{i:04d}", None)
    for i in range(count - hot_ids):
        apply(store, "remember", f"seed-{i:06d}", None)
    close_store(store)


def integrity_report(db_path: Path) -> dict:
    con = _connect(Path(db_path), BUSY_TIMEOUT_MS / 1000.0)
    report = {
        "integrity_check": con.execute("PRAGMA integrity_check").fetchone()[0],
        "foreign_key_check": len(con.execute("PRAGMA foreign_key_check").fetchall()),
        "migrations": [r[0] for r in con.execute("SELECT version FROM schema_migrations")],
        "memories": con.execute("SELECT COUNT(*) FROM memories").fetchone()[0],
        "fts_rows": con.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0],
    }
    report["fts_parity"] = report["memories"] == report["fts_rows"]
    con.close()
    return report
