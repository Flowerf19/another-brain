"""Transactional SQLite memory/lifecycle/audit repository."""
from __future__ import annotations

import json
import re
import sqlite3
import struct
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Sequence

from ..domain.models import MemoryRecord, MemoryScope, SearchFilters, timeline_day
from ..domain.retention import expires_at_ms
from ..errors import ValidationError
from .connection import SQLiteConnectionFactory

PROFILE_ID = "harrier-q4-v2"
MODEL_NAME = "onnx-community/harrier-oss-v1-270m-ONNX"
MODEL_REVISION = "d59c919d0159aea2c19ed7d04288fcdd048d0f9c"
_FORBIDDEN_AUDIT_KEYS = {"topic", "summary", "content", "metadata"}
_TERM_RE = re.compile(r"[^\W_]+", re.UNICODE)


def pack_vector(values: Sequence[float]) -> bytes:
    if len(values) != 640:
        raise ValidationError(f"embedding dimension must be 640, got {len(values)}")
    return struct.pack("<640f", *values)


def unpack_vector(blob: bytes) -> tuple[float, ...]:
    if not isinstance(blob, (bytes, bytearray)) or len(blob) != 2_560:
        raise ValidationError("stored embedding must be a 2560-byte FLOAT32 vector")
    return struct.unpack("<640f", blob)


def safe_fts_query(text: str) -> str | None:
    terms = []
    seen = set()
    for term in _TERM_RE.findall(text.casefold()):
        if term not in seen:
            terms.append(term.replace('"', '""'))
            seen.add(term)
    return " OR ".join(f'"{term}"' for term in terms) or None


class SQLiteRepository:
    def __init__(self, path: Path, *, timezone: str, audit_retention_days: int = 90):
        self.factory = SQLiteConnectionFactory(path)
        self.timezone = timezone
        self.audit_retention_days = audit_retention_days
        self.factory.bootstrap()
        self._ensure_profile()

    def _ensure_profile(self) -> None:
        with self.factory.connect() as db:
            db.execute(
                """INSERT OR IGNORE INTO embedding_profiles
                (profile_id,model_name,revision,variant,dimension,dtype,normalized,
                 input_version,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    PROFILE_ID,
                    MODEL_NAME,
                    MODEL_REVISION,
                    "q4",
                    640,
                    "float32-le",
                    1,
                    2,
                    int(time.time() * 1000),
                ),
            )

    def store(self, record: MemoryRecord, embedding: Sequence[float]) -> None:
        with self.factory.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    """INSERT INTO memories (
                    memory_id,brain_id,agent_id,scope,scope_id,topic,catalog,summary,
                    content,timeline_day,period_start,period_end,created_at,updated_at,
                    importance,expires_at,deleted_at,metadata_json,profile_id,embedding,
                    record_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record.memory_id,
                        record.brain_id,
                        record.agent_id,
                        record.scope.value,
                        record.scope_id,
                        record.topic,
                        record.catalog,
                        record.summary,
                        record.content,
                        record.timeline_day,
                        record.period_start_ms,
                        record.period_end_ms,
                        record.created_at_ms,
                        record.updated_at_ms,
                        record.importance,
                        record.expires_at_ms,
                        record.deleted_at_ms,
                        json.dumps(record.metadata, ensure_ascii=False, allow_nan=False),
                        PROFILE_ID,
                        pack_vector(embedding),
                        record.record_version,
                    ),
                )
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def get(self, brain_id: str, memory_id: str, *, now_ms: int | None = None) -> MemoryRecord | None:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        with self.factory.connect() as db:
            row = db.execute(
                """SELECT * FROM memories WHERE brain_id=? AND memory_id=?
                AND deleted_at IS NULL AND expires_at>?""",
                (brain_id, memory_id, now),
            ).fetchone()
        return self._map(row) if row else None

    def expire_at(self, brain_id: str, memory_id: str) -> int | None:
        with self.factory.connect() as db:
            row = db.execute(
                "SELECT expires_at FROM memories WHERE brain_id=? AND memory_id=?",
                (brain_id, memory_id),
            ).fetchone()
        return int(row[0]) if row else None

    def recent(
        self,
        brain_id: str,
        filters: SearchFilters,
        limit: int,
        *,
        now_ms: int | None = None,
    ) -> list[MemoryRecord]:
        now = int(time.time() * 1000) if now_ms is None else int(now_ms)
        where, params = self._live_where(brain_id, filters, now)
        with self.factory.connect() as db:
            rows = db.execute(
                f"SELECT * FROM memories WHERE {where} "
                "ORDER BY created_at DESC,memory_id ASC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._map(row) for row in rows]

    def reinforce(
        self, brain_id: str, memory_id: str, *, now_ms: int
    ) -> MemoryRecord | None:
        with self.factory.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    """SELECT importance FROM memories WHERE brain_id=? AND memory_id=?
                    AND deleted_at IS NULL AND expires_at>?""",
                    (brain_id, memory_id, now_ms),
                ).fetchone()
                if row is None:
                    db.execute("ROLLBACK")
                    return None
                db.execute(
                    """UPDATE memories SET updated_at=?,expires_at=?
                    WHERE brain_id=? AND memory_id=?""",
                    (
                        now_ms,
                        expires_at_ms(int(row["importance"]), now_ms),
                        brain_id,
                        memory_id,
                    ),
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self.get(brain_id, memory_id, now_ms=now_ms)

    def soft_delete(self, brain_id: str, memory_id: str, *, now_ms: int, grace_ms: int) -> bool:
        with self.factory.connect() as db:
            cursor = db.execute(
                """UPDATE memories SET deleted_at=?,expires_at=min(expires_at,?)
                WHERE brain_id=? AND memory_id=? AND deleted_at IS NULL AND expires_at>?""",
                (now_ms, now_ms + grace_ms, brain_id, memory_id, now_ms),
            )
            return cursor.rowcount == 1

    def restore(self, brain_id: str, memory_id: str, *, now_ms: int) -> MemoryRecord | None:
        with self.factory.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    """SELECT importance FROM memories WHERE brain_id=? AND memory_id=?
                    AND deleted_at IS NOT NULL AND expires_at>?""",
                    (brain_id, memory_id, now_ms),
                ).fetchone()
                if row is None:
                    db.execute("ROLLBACK")
                    return None
                db.execute(
                    """UPDATE memories SET deleted_at=NULL,updated_at=?,expires_at=?
                    WHERE brain_id=? AND memory_id=?""",
                    (
                        now_ms,
                        expires_at_ms(int(row["importance"]), now_ms),
                        brain_id,
                        memory_id,
                    ),
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self.get(brain_id, memory_id, now_ms=now_ms)

    def hard_delete(self, brain_id: str, memory_id: str) -> bool:
        with self.factory.connect() as db:
            return db.execute(
                "DELETE FROM memories WHERE brain_id=? AND memory_id=?",
                (brain_id, memory_id),
            ).rowcount == 1

    def lexical_candidates(
        self,
        brain_id: str,
        filters: SearchFilters,
        query: str,
        *,
        now_ms: int,
        limit: int,
    ) -> list[tuple[MemoryRecord, float]]:
        match = safe_fts_query(query)
        if not match:
            return []
        where, params = self._live_where(brain_id, filters, now_ms, prefix="m.")
        sql = f"""SELECT m.*,bm25(memory_fts,5.0,3.0,1.0) AS lexical_score
        FROM memory_fts JOIN memories m ON m.row_id=memory_fts.rowid
        WHERE memory_fts MATCH ? AND {where}
        ORDER BY lexical_score ASC,m.memory_id ASC LIMIT ?"""
        with self.factory.connect() as db:
            rows = db.execute(sql, (match, *params, limit)).fetchall()
        return [(self._map(row), float(row["lexical_score"])) for row in rows]

    def vector_rows(
        self,
        brain_id: str,
        filters: SearchFilters,
        *,
        now_ms: int,
    ) -> list[tuple[MemoryRecord, tuple[float, ...]]]:
        where, params = self._live_where(brain_id, filters, now_ms)
        with self.factory.connect() as db:
            rows = db.execute(
                f"SELECT * FROM memories WHERE {where}", params
            ).fetchall()
        return [(self._map(row), unpack_vector(row["embedding"])) for row in rows]

    def record_audit(
        self,
        *,
        brain_id: str,
        memory_id: str,
        agent_id: str,
        action: str,
        event_at_ms: int,
        detail: dict[str, Any] | None = None,
    ) -> None:
        payload = dict(detail or {})
        leaked = _FORBIDDEN_AUDIT_KEYS & payload.keys()
        if leaked:
            raise ValidationError(f"audit detail contains forbidden fields: {sorted(leaked)}")
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        try:
            with self.factory.connect() as db:
                db.execute(
                    "INSERT INTO audit_events VALUES (?,?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        brain_id,
                        memory_id,
                        agent_id,
                        action,
                        event_at_ms,
                        timeline_day(event_at_ms, self.timezone),
                        raw,
                    ),
                )
                cutoff = event_at_ms - self.audit_retention_days * 86_400_000
                db.execute("DELETE FROM audit_events WHERE event_at<?", (cutoff,))
        except sqlite3.Error:
            # Audit is best effort and must not roll back an already committed mutation.
            return

    def list_audit(self, brain_id: str, day: str, *, limit: int) -> list[dict[str, Any]]:
        with self.factory.connect() as db:
            rows = db.execute(
                """SELECT * FROM audit_events WHERE brain_id=? AND timeline_day=?
                ORDER BY event_at DESC,event_id ASC LIMIT ?""",
                (brain_id, day, limit),
            ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "memory_id": row["memory_id"],
                "agent_id": row["agent_id"],
                "action": row["action"],
                "event_at_ms": row["event_at"],
                "detail": json.loads(row["detail_json"]),
            }
            for row in rows
        ]

    def health(self) -> dict[str, Any]:
        with self.factory.connect() as db:
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            count = db.execute("SELECT count(*) FROM memories").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
        return {
            "database": str(self.factory.path),
            "schema_version": int(version),
            "integrity": integrity,
            "memory_count": int(count),
            "fts5": True,
            "vector_backend": "numpy-exact",
        }

    @staticmethod
    def _map(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["memory_id"],
            brain_id=row["brain_id"],
            agent_id=row["agent_id"],
            scope=MemoryScope(row["scope"]),
            scope_id=row["scope_id"],
            topic=row["topic"],
            catalog=row["catalog"],
            summary=row["summary"],
            content=row["content"],
            timeline_day=row["timeline_day"],
            period_start_ms=int(row["period_start"]),
            period_end_ms=int(row["period_end"]),
            created_at_ms=int(row["created_at"]),
            updated_at_ms=int(row["updated_at"]),
            importance=int(row["importance"]),
            expires_at_ms=int(row["expires_at"]),
            deleted_at_ms=row["deleted_at"],
            metadata=json.loads(row["metadata_json"]),
            record_version=int(row["record_version"]),
        )

    @staticmethod
    def _live_where(
        brain_id: str,
        filters: SearchFilters,
        now_ms: int,
        *,
        prefix: str = "",
    ) -> tuple[str, list[Any]]:
        clauses = [
            f"{prefix}brain_id=?",
            f"{prefix}scope=?",
            f"{prefix}scope_id=?",
            f"{prefix}deleted_at IS NULL",
            f"{prefix}expires_at>?",
        ]
        params: list[Any] = [brain_id, filters.scope.value, filters.scope_id, now_ms]
        for column, value in (
            ("topic", filters.topic),
            ("catalog", filters.catalog),
            ("timeline_day", filters.timeline_day),
        ):
            if value is not None:
                clauses.append(f"{prefix}{column}=?")
                params.append(value)
        if filters.min_importance is not None:
            clauses.append(f"{prefix}importance>=?")
            params.append(filters.min_importance)
        if filters.since_ms is not None:
            clauses.append(f"{prefix}period_start>=?")
            params.append(filters.since_ms)
        return " AND ".join(clauses), params
