"""SQLite ``AuditRepository`` — structural facts, 90-day retention (TASK-053).

Events are secret-free: forbidden memory-text keys (``topic``/``summary``/
``content``/``metadata``) are rejected recursively by :class:`AuditEvent`
before any write. ``audit_events`` has no memory FK, so hard-delete and
expired-skip preserve history. Day reads are deterministic
``event_at DESC, event_id ASC``. Retention is a fixed 90 days by
``event_at`` with a bounded opportunistic cleanup — and audit failures never
roll back an already committed memory mutation (best-effort isolation: the
service records audit after the mutation commits; a failure here raises but
the memory row stands).
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Sequence

from another_brain.config import AUDIT_RETENTION_DAYS
from another_brain.domain.models import AuditAction, AuditEvent
from another_brain.errors import StorageError, ValidationError
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.retry import busy_retry

_DAY_MS = 86_400_000
DEFAULT_RETENTION_BATCH = 500

_EVENT_COLUMNS = (
    "event_id", "brain_id", "memory_id", "agent_id", "action",
    "event_at_ms", "timeline_day", "detail_json",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row_to_event(row: Sequence) -> AuditEvent:
    values = dict(zip(_EVENT_COLUMNS, row))
    values["action"] = AuditAction(values["action"])
    try:
        values["detail"] = json.loads(values.pop("detail_json"))
    except (TypeError, ValueError) as exc:
        raise StorageError(
            f"corrupt detail_json in audit row {values['event_id']!r}: {exc}"
        ) from exc
    return AuditEvent(**values)


class SQLiteAuditRepository:
    """Audit persistence bound to one ``brain_id``."""

    def __init__(
        self,
        factory: SQLiteConnectionFactory,
        *,
        brain_id: str,
        clock: Callable[[], int] = _now_ms,
    ) -> None:
        self._factory = factory
        self._brain_id = brain_id
        self._clock = clock

    def record(self, event: AuditEvent) -> None:
        """Persist one structural mutation fact (forbidden text rejected)."""
        if event.brain_id != self._brain_id:
            raise ValidationError(
                f"event brain_id {event.brain_id!r} does not match bound"
                f" brain {self._brain_id!r}"
            )
        try:
            detail_json = json.dumps(
                event.detail, sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                f"detail must be JSON-serializable: {exc}"
            ) from exc

        with self._factory.connect() as con:
            raw = con.connection

            def _tx() -> None:
                raw.execute("BEGIN IMMEDIATE")
                try:
                    raw.execute(
                        "INSERT INTO audit_events(event_id, brain_id, memory_id,"
                        " agent_id, action, event_at_ms, timeline_day, detail_json)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            event.event_id, event.brain_id, event.memory_id,
                            event.agent_id, event.action.value, event.event_at_ms,
                            event.timeline_day, detail_json,
                        ),
                    )
                    raw.commit()
                except Exception:
                    raw.rollback()
                    raise

            busy_retry(_tx)

    def list_day(self, day: str) -> Sequence[AuditEvent]:
        """Events for ``(bound brain_id, day)``, newest first, ties by id."""
        with self._factory.connect(read_only=True) as con:
            rows = con.connection.execute(
                "SELECT event_id, brain_id, memory_id, agent_id, action,"
                " event_at_ms, timeline_day, detail_json FROM audit_events"
                " WHERE brain_id = ? AND timeline_day = ?"
                " ORDER BY event_at_ms DESC, event_id ASC",
                (self._brain_id, day),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def purge_retained(self, *, max_rows: int = DEFAULT_RETENTION_BATCH) -> int:
        """Bounded cleanup of events older than the fixed 90-day retention."""
        if max_rows < 1:
            raise ValidationError(f"max_rows must be >= 1, got {max_rows}")
        cutoff = self._clock() - AUDIT_RETENTION_DAYS * _DAY_MS
        with self._factory.connect() as con:
            raw = con.connection

            def _tx() -> int:
                raw.execute("BEGIN IMMEDIATE")
                try:
                    cursor = raw.execute(
                        "DELETE FROM audit_events WHERE event_id IN ("
                        "  SELECT event_id FROM audit_events"
                        "  WHERE event_at_ms < ? LIMIT ?)",
                        (cutoff, max_rows),
                    )
                    raw.commit()
                    return cursor.rowcount
                except Exception:
                    raw.rollback()
                    raise

            return busy_retry(_tx)  # type: ignore[return-value]
