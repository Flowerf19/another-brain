"""Real-repository adapter for the concurrency harness (TASK-055).

Applies the accepted workload (fresh-open storm, mixed WAL writers/readers,
crash probe, busy-exhaustion probe) to the production SQLite stack:
``SQLiteConnectionFactory.bootstrap()`` + ``migrate()`` +
``SQLiteMemoryRepository`` with the locked busy-retry envelope — no toy
shortcuts, no harness-only code paths.

Differences from the toy adapter, by design:

- remember is append-only: a racing/preseeded ``(brain_id, memory_id)``
  raises :class:`DuplicateMemoryError`, which the allowed-outcome oracle
  records as the ``duplicate`` race instead of an error;
- lifecycle ops return ``MutationOutcome`` — ``not_found`` is an allowed
  serializable race outcome, never an error;
- readers exercise the real ``get``/``recent`` repository reads. ``search``
  runs the FTS5 MATCH + join + live-filter SQL directly because the locked
  lexical module lands in GOAL-012 (TASK-057); here it proves trigger
  parity and live filtering under concurrency, not ranking;
- fake embeddings are deterministic per ``memory_id`` (sha256-seeded,
  unit-norm FLOAT32[640]); the harness never loads ONNX (TASK-007 contract);
- the sqlite-vec capability is probed per connection in ``extension`` mode
  and skipped in forced ``numpy`` mode (``AB_CONC_VEC`` env var, inherited
  by spawned workers). Vector search itself lands with GOAL-012.

The retry/timeout constructor kwargs are accepted for harness-interface
compatibility; the real repository applies the locked constants from
``config``/``retry.py`` — that locked envelope is exactly what the
busy-exhaustion probe measures.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from another_brain.domain.models import EmbeddingVector, MemoryRecord
from another_brain.errors import BusyExhausted, DuplicateMemoryError
from another_brain.protocols import MutationOutcome
from another_brain.services.embedding.model_manifest import MODEL_MANIFEST
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.migrations import migrate
from another_brain.services.sql.repository import SQLiteMemoryRepository
from another_brain.services.sql.retry import busy_retry
from another_brain.services.sql.schema import checksum
from another_brain.services.sql.ttl import ttl_ms_for

BRAIN_ID = "conc-brain"
AGENT_ID = "conc-agent"
VEC_MODE_ENV = "AB_CONC_VEC"

_SEARCH_SQL = (
    "SELECT m.memory_id FROM memory_fts f CROSS JOIN memories m ON m.row_id = f.rowid"
    " WHERE memory_fts MATCH ? AND m.brain_id = ?"
    " AND m.deleted_at_ms IS NULL AND m.expires_at_ms > ?"
    " ORDER BY bm25(memory_fts, 5.0, 3.0, 1.0), m.memory_id ASC LIMIT 50"
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _vector_for(memory_id: str) -> EmbeddingVector:
    """Deterministic unit-norm FLOAT32[640] fake embedding (never ONNX)."""
    digest = hashlib.sha256(memory_id.encode("utf-8")).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
    values = rng.standard_normal(640, dtype=np.float32)
    values /= np.linalg.norm(values)
    return EmbeddingVector(values=values)


def _record(memory_id: str, *, now_ms: int) -> MemoryRecord:
    importance = int(hashlib.sha256(memory_id.encode()).digest()[8]) % 5 + 1
    return MemoryRecord(
        memory_id=memory_id,
        brain_id=BRAIN_ID,
        agent_id=AGENT_ID,
        topic="concurrency-hot-ids",
        catalog="workload",
        summary=f"workload payload {memory_id}",
        content=f"content body for {memory_id}",
        timeline_day=datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
        .date()
        .isoformat(),
        period_start_ms=None,
        period_end_ms=None,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        importance=importance,
        expires_at_ms=now_ms + ttl_ms_for(importance),
        deleted_at_ms=None,
        metadata={"fixture": "concurrency"},
        profile_id=MODEL_MANIFEST.profile,
        record_version=1,
        embedding=_vector_for(memory_id),
    )


class Store:
    """One worker process's handle: factory + bound real repository."""

    def __init__(self, factory: SQLiteConnectionFactory, vec_mode: str) -> None:
        self.factory = factory
        self.repo = SQLiteMemoryRepository(factory, brain_id=BRAIN_ID)
        self.vec_mode = vec_mode


def open_store(
    db_path: Path,
    *,
    busy_timeout_s: float = 5.0,
    retry_attempts: int = 5,
    retry_base_s: float = 0.05,
) -> Store:
    """Bootstrap + migrate + open the real bound repository.

    The retry kwargs mirror the toy adapter's interface; the production
    envelope is locked in ``config``/``retry.py`` and applied as-is.
    """
    del busy_timeout_s, retry_attempts, retry_base_s  # locked in production code
    factory = SQLiteConnectionFactory(Path(db_path))
    factory.bootstrap()
    migrate(factory.db_path)
    vec_mode = os.environ.get(VEC_MODE_ENV, "extension")
    store = Store(factory, vec_mode)
    if vec_mode == "extension":
        with factory.connect() as con:
            con.load_vec()  # per-connection capability probe, never fatal
    return store


def close_store(store: Store) -> None:
    """Connections are per-operation; nothing long-lived to close."""


def apply(store: Store, op: str, memory_id: str, rng) -> str:
    repo = store.repo
    if op == "remember":
        try:
            repo.store(_record(memory_id, now_ms=_now_ms()))
        except DuplicateMemoryError:
            return "duplicate"  # allowed race: preseed or another writer won
        return "remembered"
    if op == "reinforce":
        outcome = repo.reinforce(memory_id)
        return "reinforced" if outcome is MutationOutcome.APPLIED else "not_found"
    if op == "forget":
        outcome = repo.soft_delete(memory_id)
        return "forgotten" if outcome is MutationOutcome.APPLIED else "not_found"
    if op == "restore":
        outcome = repo.restore(memory_id)
        return "restored" if outcome is MutationOutcome.APPLIED else "not_found"
    if op == "get":
        return "hit" if repo.get(memory_id) is not None else "not_found"
    if op == "recent":
        repo.recent(limit=5)
        return "listed"
    if op == "search":
        with store.factory.connect(read_only=True) as con:
            con.connection.execute(
                _SEARCH_SQL,
                ('"payload"', BRAIN_ID, _now_ms()),
            ).fetchall()
        return "searched"
    raise ValueError(f"unknown op {op}")


def ensure_profile(db_path: Path) -> None:
    """Idempotently insert the manifest-derived ``embedding_profiles`` row."""
    factory = SQLiteConnectionFactory(Path(db_path))
    with factory.connect() as con:
        raw = con.connection

        def _tx() -> None:
            raw.execute("BEGIN IMMEDIATE")
            try:
                raw.execute(
                    "INSERT OR IGNORE INTO embedding_profiles(profile_id,"
                    " model_repo, model_revision, variant, dimension, dtype,"
                    " normalized, tokenizer_sha256, config_sha256,"
                    " prompt_utf8_sha256, query_prompt, input_version,"
                    " created_at_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        MODEL_MANIFEST.profile, MODEL_MANIFEST.repo,
                        MODEL_MANIFEST.revision, MODEL_MANIFEST.profile,
                        MODEL_MANIFEST.dimensions, MODEL_MANIFEST.dtype, 1,
                        dict(MODEL_MANIFEST.files)["tokenizer.json"],
                        dict(MODEL_MANIFEST.files)["config.json"],
                        MODEL_MANIFEST.query_prompt_utf8_sha256,
                        MODEL_MANIFEST.query_prompt,
                        MODEL_MANIFEST.input_version, _now_ms(),
                    ),
                )
                raw.commit()
            except Exception:
                raw.rollback()
                raise

        busy_retry(_tx)


def preseed(db_path: Path, count: int, hot_ids: int) -> None:
    store = open_store(db_path)
    ensure_profile(db_path)
    now = _now_ms()
    ids = [f"hot-{i:04d}" for i in range(1, hot_ids + 1)]
    ids += [f"seed-{i:06d}" for i in range(count - hot_ids)]
    for memory_id in ids:
        store.repo.store(_record(memory_id, now_ms=now))
    close_store(store)


def integrity_report(db_path: Path) -> dict:
    """Post-run oracle data: integrity, ledger, FTS parity, live filtering."""
    raw = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    try:
        now = _now_ms()
        ledger = list(raw.execute("SELECT version, checksum FROM schema_migrations"))
        memories = raw.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        fts_rows = raw.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
        # Deep parity: every memory row mirrors its FTS row exactly (NULL-safe).
        mismatched = raw.execute(
            "SELECT COUNT(*) FROM memories m"
            " LEFT JOIN memory_fts f ON f.rowid = m.row_id"
            " WHERE f.topic IS NOT m.topic OR f.summary IS NOT m.summary"
            "    OR f.content IS NOT m.content"
        ).fetchone()[0]
        orphans = raw.execute(
            "SELECT COUNT(*) FROM memory_fts f"
            " LEFT JOIN memories m ON m.row_id = f.rowid WHERE m.row_id IS NULL"
        ).fetchone()[0]
        non_live = {
            r[0]
            for r in raw.execute(
                "SELECT memory_id FROM memories"
                " WHERE deleted_at_ms IS NOT NULL OR expires_at_ms <= ?",
                (now,),
            )
        }
        hits = {
            r[0]
            for r in raw.execute(
                _SEARCH_SQL,
                ('"payload"', BRAIN_ID, now),
            )
        }
        profiles = raw.execute("SELECT COUNT(*) FROM embedding_profiles").fetchone()[0]
        return {
            "integrity_check": raw.execute("PRAGMA integrity_check").fetchone()[0],
            "foreign_key_violations": len(
                raw.execute("PRAGMA foreign_key_check").fetchall()
            ),
            "ledger": ledger,
            "ledger_ok": ledger == [(1, checksum())],
            "memories": memories,
            "fts_rows": fts_rows,
            "fts_parity": memories == fts_rows and mismatched == 0 and orphans == 0,
            "live_filter_ok": not (non_live & hits),
            "profiles": profiles,
        }
    finally:
        raw.close()


def restart_probe(db_path: Path) -> bool:
    """Fresh factory on the existing file: read + full write lifecycle works."""
    factory = SQLiteConnectionFactory(Path(db_path))
    factory.bootstrap()
    migrate(factory.db_path)
    repo = SQLiteMemoryRepository(factory, brain_id=BRAIN_ID)
    probe_id = "restart-probe"
    existing = repo.get(probe_id)
    if existing is None:
        repo.store(_record(probe_id, now_ms=_now_ms()))
    ok = repo.get(probe_id) is not None
    ok = ok and repo.reinforce(probe_id) is MutationOutcome.APPLIED
    ok = ok and repo.soft_delete(probe_id) is MutationOutcome.APPLIED
    repo.hard_delete(probe_id)
    return ok
