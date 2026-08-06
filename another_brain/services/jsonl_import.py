"""JSONL v1 envelope parsing/validation and import orchestration (TASK-071/072,
contract .agents/contracts/another-brain-jsonl-v1.md).

Import counters: ``imported`` = rows inserted this run; ``skipped`` =
expired-memory skips plus same-key/same-fields duplicates; identity or field
conflicts raise :class:`JsonlImportConflictError` after marking the run
``failed``.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from another_brain.errors import ValidationError

TIMELINE_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AUDIT_ACTIONS = {"remember", "reinforce", "forget", "restore", "hard_delete"}
FORBIDDEN_AUDIT_KEYS = {"topic", "summary", "content", "metadata"}

MANIFEST_KEYS = {
    "kind", "format", "format_version", "export_id", "source_app_version",
    "source_schema_version", "source_commit", "source_embedding_profile",
    "exported_at_ms", "expiry_mode", "memory_count", "audit_count",
}
DATA_KEYS = {"seq", "kind", "idempotency_key", "payload", "payload_sha256"}
MEMORY_PAYLOAD_KEYS = {
    "memory_id", "brain_id", "agent_id", "topic",
    "catalog", "summary", "content", "timeline_day", "period_start_ms",
    "period_end_ms", "created_at_ms", "updated_at_ms", "importance",
    "expires_at_ms", "deleted_at_ms", "metadata", "record_version",
    "remaining_ttl_ms",
}
AUDIT_PAYLOAD_KEYS = {
    "event_id", "brain_id", "memory_id", "agent_id", "action", "event_at_ms",
    "detail",
}
TRAILER_KEYS = {"kind", "memory_count", "audit_count", "last_seq", "rolling_sha256"}


class JsonlEnvelopeError(ValidationError):
    """The file violates the frozen another-brain-jsonl v1 envelope contract."""


@dataclass(frozen=True)
class Envelope:
    """A validated JSONL v1 envelope, ready for TASK-072 import orchestration."""

    manifest: dict[str, Any]
    data_lines: list[tuple[int, str, dict[str, Any]]]
    trailer: dict[str, Any]
    artifact_sha256: str
    exported_at_ms: int
    export_id: str


def canonical(obj: object) -> str:
    """Serialize an object exactly as the contract requires."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number literal {value!r}")


def _parse_line(raw: str, line_no: int) -> dict[str, Any]:
    try:
        obj = json.loads(raw, parse_constant=_reject_constant)
    except ValueError as exc:
        raise JsonlEnvelopeError(f"line {line_no}: invalid JSON ({exc})") from None
    if not isinstance(obj, dict):
        raise JsonlEnvelopeError(f"line {line_no}: expected a JSON object")
    if canonical(obj) != raw:
        raise JsonlEnvelopeError(
            f"line {line_no}: line is not canonical JSON"
            " (sorted keys, compact separators, ensure_ascii=false)"
        )
    return obj


def _check_int(
    value: object, name: str, where: str, *, nullable: bool = False
) -> None:
    if nullable and value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise JsonlEnvelopeError(f"{where}: {name} must be an integer, got {value!r}")


def verify_remaining_ttl(payload: dict[str, Any], exported_at_ms: int) -> None:
    """Reject a memory payload whose remaining_ttl_ms drifts from the absolute
    TTL by more than the 1,000 ms tolerance allowed for legacy source
    resolution (contract: "may differ by at most 1,000 ms").

    The exporter records ``remaining_ttl_ms = max(0, expires_at_ms -
    exported_at_ms)`` exactly; the relative verifier tolerates ±1000 ms.
    """
    expected = max(0, int(payload["expires_at_ms"]) - exported_at_ms)
    if abs(payload["remaining_ttl_ms"] - expected) > 1000:
        raise JsonlEnvelopeError(
            f"remaining_ttl_ms {payload['remaining_ttl_ms']} differs from"
            f" max(0, expires_at_ms - exported_at_ms) = {expected} by more than"
            " 1000 ms"
        )


def _check_memory_payload(
    payload: dict[str, Any], exported_at_ms: int, where: str
) -> None:
    if set(payload) != MEMORY_PAYLOAD_KEYS:
        raise JsonlEnvelopeError(
            f"{where}: memory payload keys differ from the contract"
            f" (extra={sorted(set(payload) - MEMORY_PAYLOAD_KEYS)},"
            f" missing={sorted(MEMORY_PAYLOAD_KEYS - set(payload))})"
        )
    if not TIMELINE_DAY_RE.match(str(payload["timeline_day"])):
        raise JsonlEnvelopeError(f"{where}: timeline_day must be YYYY-MM-DD")
    for key in ("created_at_ms", "updated_at_ms", "expires_at_ms", "remaining_ttl_ms"):
        _check_int(payload[key], key, where)
    for key in ("period_start_ms", "period_end_ms", "deleted_at_ms"):
        _check_int(payload[key], key, where, nullable=True)
    if (
        payload["period_start_ms"] is not None
        and payload["period_end_ms"] is not None
        and payload["period_end_ms"] < payload["period_start_ms"]
    ):
        raise JsonlEnvelopeError(f"{where}: period_end_ms < period_start_ms")
    if isinstance(payload["importance"], bool) or not 1 <= payload["importance"] <= 5:
        raise JsonlEnvelopeError(f"{where}: importance must be 1..5")
    if not isinstance(payload["metadata"], dict):
        raise JsonlEnvelopeError(f"{where}: metadata must be an object")
    verify_remaining_ttl(payload, exported_at_ms)


def _check_audit_payload(payload: dict[str, Any], where: str) -> None:
    if set(payload) != AUDIT_PAYLOAD_KEYS:
        raise JsonlEnvelopeError(
            f"{where}: audit payload keys differ from the contract"
            f" (extra={sorted(set(payload) - AUDIT_PAYLOAD_KEYS)},"
            f" missing={sorted(AUDIT_PAYLOAD_KEYS - set(payload))})"
        )
    if FORBIDDEN_AUDIT_KEYS & set(payload):
        raise JsonlEnvelopeError(
            f"{where}: audit payload carries forbidden memory-text keys"
        )
    if payload["action"] not in AUDIT_ACTIONS:
        raise JsonlEnvelopeError(f"{where}: invalid audit action {payload['action']!r}")
    _check_int(payload["event_at_ms"], "event_at_ms", where)
    if not isinstance(payload["detail"], dict):
        raise JsonlEnvelopeError(f"{where}: detail must be an object")


def parse_envelope(path: Path) -> Envelope:
    """Parse and validate a JSONL v1 envelope per the frozen contract.

    Raises :class:`JsonlEnvelopeError` on the first violation.
    """
    data = path.read_bytes()
    artifact_sha256 = hashlib.sha256(data).hexdigest()
    if b"\r" in data:
        raise JsonlEnvelopeError("CR line ending found; envelope is LF-only")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JsonlEnvelopeError(f"not valid UTF-8: {exc}") from None
    if not text.endswith("\n"):
        raise JsonlEnvelopeError("file must end with a trailing LF")
    lines = text[:-1].split("\n")
    if len(lines) < 2:
        raise JsonlEnvelopeError("envelope needs at least a manifest and a trailer")

    rolling = hashlib.sha256()
    manifest = _parse_line(lines[0], 1)
    rolling.update(lines[0].encode("utf-8") + b"\n")
    if set(manifest) != MANIFEST_KEYS:
        raise JsonlEnvelopeError(
            "line 1: manifest keys differ from the contract"
            f" (extra={sorted(set(manifest) - MANIFEST_KEYS)},"
            f" missing={sorted(MANIFEST_KEYS - set(manifest))})"
        )
    if manifest["kind"] != "manifest":
        raise JsonlEnvelopeError("line 1: kind must be 'manifest'")
    if manifest["format"] != "another-brain-jsonl":
        raise JsonlEnvelopeError("line 1: format must be 'another-brain-jsonl'")
    if manifest["format_version"] != 1:
        raise JsonlEnvelopeError("line 1: format_version must be 1")
    if manifest["expiry_mode"] != "absolute_epoch_ms":
        raise JsonlEnvelopeError("line 1: expiry_mode must be 'absolute_epoch_ms'")
    try:
        uuid.UUID(str(manifest["export_id"]))
    except ValueError:
        raise JsonlEnvelopeError("line 1: export_id must be a UUID") from None
    _check_int(manifest["exported_at_ms"], "exported_at_ms", "line 1")
    for key in ("memory_count", "audit_count"):
        _check_int(manifest[key], key, "line 1")
        if manifest[key] < 0:
            raise JsonlEnvelopeError(f"line 1: {key} must be non-negative")

    data_lines_raw = lines[1:-1]
    trailer = _parse_line(lines[-1], len(lines))
    if len(data_lines_raw) != manifest["memory_count"] + manifest["audit_count"]:
        raise JsonlEnvelopeError(
            f"data line count {len(data_lines_raw)} != memory_count + audit_count"
            f" ({manifest['memory_count']} + {manifest['audit_count']})"
        )

    data_lines: list[tuple[int, str, dict[str, Any]]] = []
    memories: list[tuple[str, str]] = []
    audits: list[tuple[str, int, str]] = []
    seen_audit = False
    for offset, raw in enumerate(data_lines_raw):
        line_no = offset + 2
        where = f"line {line_no}"
        obj = _parse_line(raw, line_no)
        rolling.update(raw.encode("utf-8") + b"\n")
        if set(obj) != DATA_KEYS:
            raise JsonlEnvelopeError(f"{where}: data line keys differ from the contract")
        if obj["seq"] != offset + 1:
            raise JsonlEnvelopeError(
                f"{where}: seq must be contiguous from 1 (got {obj['seq']})"
            )
        payload = obj["payload"]
        if not isinstance(payload, dict):
            raise JsonlEnvelopeError(f"{where}: payload must be an object")
        digest = hashlib.sha256(canonical(payload).encode("utf-8")).hexdigest()
        if obj["payload_sha256"] != digest:
            raise JsonlEnvelopeError(f"{where}: payload_sha256 mismatch")
        kind = obj["kind"]
        if kind == "memory":
            if seen_audit:
                raise JsonlEnvelopeError(f"{where}: memory line after audit lines")
            _check_memory_payload(payload, manifest["exported_at_ms"], where)
            key = (payload["brain_id"], payload["memory_id"])
            if obj["idempotency_key"] != f"memory:{key[0]}:{key[1]}":
                raise JsonlEnvelopeError(f"{where}: bad memory idempotency_key")
            memories.append(key)
        elif kind == "audit":
            seen_audit = True
            _check_audit_payload(payload, where)
            key = (payload["brain_id"], payload["event_at_ms"], payload["event_id"])
            if obj["idempotency_key"] != f"audit:{key[0]}:{key[2]}":
                raise JsonlEnvelopeError(f"{where}: bad audit idempotency_key")
            audits.append(key)
        else:
            raise JsonlEnvelopeError(f"{where}: kind must be 'memory' or 'audit'")
        data_lines.append((obj["seq"], kind, payload))

    if memories != sorted(memories):
        raise JsonlEnvelopeError("memory lines are not sorted by (brain_id, memory_id)")
    if audits != sorted(audits):
        raise JsonlEnvelopeError(
            "audit lines are not sorted by (brain_id, event_at_ms, event_id)"
        )

    if set(trailer) != TRAILER_KEYS:
        raise JsonlEnvelopeError("trailer: keys differ from the contract")
    if trailer["kind"] != "trailer":
        raise JsonlEnvelopeError("trailer: kind must be 'trailer'")
    if trailer["memory_count"] != manifest["memory_count"] or trailer[
        "audit_count"
    ] != manifest["audit_count"]:
        raise JsonlEnvelopeError("trailer: counts differ from manifest")
    if trailer["last_seq"] != len(data_lines):
        raise JsonlEnvelopeError("trailer: last_seq must equal the number of data lines")
    if trailer["rolling_sha256"] != rolling.hexdigest():
        raise JsonlEnvelopeError("trailer: rolling_sha256 mismatch")

    return Envelope(
        manifest=manifest,
        data_lines=data_lines,
        trailer=trailer,
        artifact_sha256=artifact_sha256,
        exported_at_ms=manifest["exported_at_ms"],
        export_id=manifest["export_id"],
    )


# ---------------------------------------------------------------------------
# Import orchestration (TASK-072)
# ---------------------------------------------------------------------------

import sqlite3
import time
from collections.abc import Callable
from functools import lru_cache

from another_brain.config import AppConfig
from another_brain.domain.timeline import timeline_day_for
from another_brain.errors import StorageError
from another_brain.protocols import EmbeddingProvider
from another_brain.services.embedding.model_manifest import MODEL_MANIFEST
from another_brain.services.sql.profile import register_profile
from another_brain.services.sql.retry import busy_retry

_MEMORY_INSERT_COLUMNS = (
    "memory_id", "brain_id", "agent_id", "topic",
    "catalog", "summary", "content", "timeline_day", "period_start_ms",
    "period_end_ms", "created_at_ms", "updated_at_ms", "importance",
    "expires_at_ms", "deleted_at_ms", "metadata", "profile_id",
    "embedding", "record_version",
)
_MEMORY_PRESERVED = tuple(
    key for key in MEMORY_PAYLOAD_KEYS if key != "remaining_ttl_ms"
)
_AUDIT_INSERT_COLUMNS = (
    "event_id", "brain_id", "memory_id", "agent_id", "action",
    "event_at_ms", "timeline_day", "detail_json",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _repo_json(obj: object) -> str:
    """JSON text exactly as the repositories serialize their TEXT columns."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


@lru_cache(maxsize=1)
def _timeline_timezone() -> str:
    return AppConfig.from_env().timeline_timezone


def _timeline_day(at_ms: int) -> str:
    return timeline_day_for(at_ms, _timeline_timezone())


class JsonlImportConflictError(StorageError):
    """Same idempotency key with differing preserved fields, or a run/artifact identity clash."""


@dataclass(frozen=True)
class ImportReport:
    export_id: str
    artifact_sha256: str
    status: str  # "completed" | "noop" (conflicts raise, never return)
    imported_count: int
    skipped_count: int
    last_committed_seq: int
    detail: str


class JsonlImporter:
    """Import orchestration: no-op/conflict/resume gate, batched writes.

    Embedding happens outside transactions (batches embed their documents
    upfront); each batch is one ``BEGIN IMMEDIATE`` write that inserts rows,
    advances ``last_committed_seq`` and persists counters atomically.
    """

    def __init__(
        self,
        factory,
        *,
        embedder: EmbeddingProvider,
        clock: Callable[[], int] = _now_ms,
        batch_size: int = 128,
        after_batch_commit=None,
    ) -> None:
        self._factory = factory
        self._embedder = embedder
        self._clock = clock
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        self._batch_size = batch_size
        self._after_batch_commit = after_batch_commit

    # -- conflict helpers -----------------------------------------------------

    def _mark_failed(self) -> None:
        with self._factory.connect() as con:
            raw = con.connection

            def _tx() -> None:
                raw.execute("BEGIN IMMEDIATE")
                try:
                    raw.execute(
                        "UPDATE import_runs SET status = 'failed',"
                        " failed_count = failed_count + 1,"
                        " completed_at_ms = ? WHERE export_id = ?",
                        (self._clock(), self._export_id),
                    )
                    raw.commit()
                except Exception:
                    raw.rollback()
                    raise

            busy_retry(_tx)

    @staticmethod
    def _memory_values(payload: dict[str, Any], embedding_blob: bytes) -> tuple:
        return tuple(payload[key] for key in _MEMORY_INSERT_COLUMNS[:-4]) + (
            _repo_json(payload["metadata"]),
            MODEL_MANIFEST.profile,
            embedding_blob,
            payload["record_version"],
        )

    @staticmethod
    def _audit_values(payload: dict[str, Any]) -> tuple:
        return (
            payload["event_id"], payload["brain_id"], payload["memory_id"],
            payload["agent_id"], payload["action"], payload["event_at_ms"],
            _timeline_day(payload["event_at_ms"]),
            _repo_json(payload["detail"]),
        )

    @staticmethod
    def _same_fields(existing: dict, payload: dict[str, Any]) -> bool:
        for key in _MEMORY_PRESERVED:
            if key == "metadata":
                if existing[key] != _repo_json(payload[key]):
                    return False
            elif existing[key] != payload[key]:
                return False
        return True

    def _memory_insert_sql(self) -> str:
        cols = ", ".join(_MEMORY_INSERT_COLUMNS)
        ph = ", ".join("?" for _ in _MEMORY_INSERT_COLUMNS)
        return f"INSERT INTO memories({cols}) VALUES ({ph})"

    def _audit_insert_sql(self) -> str:
        cols = ", ".join(_AUDIT_INSERT_COLUMNS)
        ph = ", ".join("?" for _ in _AUDIT_INSERT_COLUMNS)
        return f"INSERT INTO audit_events({cols}) VALUES ({ph})"

    def _memories_existing(self, raw: sqlite3.Connection) -> dict[tuple[str, str], dict]:
        if not self._keys_in_flight:
            return {}
        cols = ", ".join(_MEMORY_PRESERVED)
        rows = raw.execute(
            f"SELECT {cols} FROM memories"
            " WHERE (brain_id, memory_id) IN (%s)"
            % ", ".join(["(?, ?)"] * len(self._keys_in_flight)),
            [v for key in self._keys_in_flight for v in key],
        ).fetchall()
        found: dict[tuple[str, str], dict] = {}
        for row in rows:
            values = dict(zip(_MEMORY_PRESERVED, row))
            found[(values["brain_id"], values["memory_id"])] = values
        return found

    def _audit_existing(self, raw: sqlite3.Connection) -> dict[str, dict]:
        if not self._audit_events:
            return {}
        rows = raw.execute(
            "SELECT event_id, action, event_at_ms, detail_json FROM audit_events"
            " WHERE event_id IN (%s)" % ", ".join(["?"] * len(self._audit_events)),
            self._audit_events,
        ).fetchall()
        return {r[0]: {"action": r[1], "event_at_ms": r[2], "detail": r[3]} for r in rows}

    # -- entry point ------------------------------------------------------------

    def import_path(self, path: Path) -> ImportReport:
        """Import one validated JSONL v1 envelope (no-op/conflict/resume gate)."""
        envelope = parse_envelope(path)
        register_profile(self._factory)
        run = self._read_run(envelope.export_id, envelope.artifact_sha256)
        if run is not None:
            return run
        if self._run_started_ms is None:
            started = self._clock()
            self._start_run(envelope, started)
        else:
            started = self._run_started_ms  # resume: keep the original start
        report = self._import_data(envelope, started)
        self._complete_run(envelope)
        return report

    def _read_run(
        self, export_id: str, artifact_sha256: str
    ) -> ImportReport | None:
        with self._factory.connect(read_only=True) as con:
            rows = con.connection.execute(
                "SELECT export_id, artifact_sha256, status, last_committed_seq,"
                " imported_count, skipped_count, failed_count, started_at_ms,"
                " completed_at_ms FROM import_runs"
            ).fetchall()
        by_id = {r[0]: r for r in rows}
        by_artifact = {r[1]: r for r in rows}
        row = by_id.get(export_id)
        if row is not None:
            if row[1] == artifact_sha256 and row[2] == "completed":
                return ImportReport(
                    export_id=export_id,
                    artifact_sha256=artifact_sha256,
                    status="noop",
                    imported_count=row[4],
                    skipped_count=row[5],
                    last_committed_seq=row[3],
                    detail="",
                )
            if row[1] != artifact_sha256:
                raise JsonlImportConflictError(
                    f"export_id {export_id!r} already imported under artifact"
                    f" sha256 {row[1][:12]}… (this file is {artifact_sha256[:12]}…)"
                )
            # Same artifact under a running/failed row: resume below.
            self._export_id = export_id
            self._artifact_sha256 = artifact_sha256
            self._run_started_ms = row[7]
            self._last_committed_seq = row[3]
            self._imported = row[4]
            self._skipped = row[5]
            return None
        if artifact_sha256 in by_artifact:
            other = by_artifact[artifact_sha256]
            raise JsonlImportConflictError(
                f"artifact sha256 {artifact_sha256[:12]}… already imported under"
                f" export_id {other[0]!r}"
            )
        self._export_id = export_id
        self._artifact_sha256 = artifact_sha256
        self._run_started_ms = None
        self._last_committed_seq = 0
        self._imported = 0
        self._skipped = 0
        return None

    def _start_run(self, envelope: Envelope, started: int) -> None:
        with self._factory.connect() as con:
            raw = con.connection

            def _tx() -> None:
                raw.execute("BEGIN IMMEDIATE")
                try:
                    raw.execute(
                        "INSERT INTO import_runs(export_id, artifact_sha256,"
                        " format_version, status, last_committed_seq,"
                        " imported_count, skipped_count, failed_count,"
                        " started_at_ms) VALUES (?,?,?,?,?,?,?,?,?)",
                        (envelope.export_id, envelope.artifact_sha256, 1,
                         "running", 0, 0, 0, 0, started),
                    )
                    raw.commit()
                except Exception:
                    raw.rollback()
                    raise

            busy_retry(_tx)
        self._run_started_ms = started

    # -- batching ---------------------------------------------------------------

    def _import_data(self, envelope: Envelope, started: int) -> ImportReport:
        start = self._last_committed_seq
        for end in range(
            start + self._batch_size, len(envelope.data_lines) + 1, self._batch_size
        ):
            self._process_batch(envelope, started, start, end)
            start = end
        if start < len(envelope.data_lines):
            self._process_batch(
                envelope, started, start, len(envelope.data_lines)
            )
        return ImportReport(
            export_id=envelope.export_id,
            artifact_sha256=envelope.artifact_sha256,
            status="completed",
            imported_count=self._imported,
            skipped_count=self._skipped,
            last_committed_seq=envelope.trailer["last_seq"],
            detail="",
        )

    def _process_batch(
        self, envelope: Envelope, started: int, start: int, end: int
    ) -> None:
        batch = envelope.data_lines[start:end]
        memory_lines = [line for line in batch if line[1] == "memory"]
        audit_lines = [line for line in batch if line[1] == "audit"]
        self._keys_in_flight = [
            (line[2]["brain_id"], line[2]["memory_id"]) for line in memory_lines
        ]
        self._audit_events = [line[2]["event_id"] for line in audit_lines]

        for line in memory_lines:
            if line[2]["expires_at_ms"] <= started:
                self._skipped += 1
        blobs = {}
        for line in memory_lines:
            if line[2]["expires_at_ms"] > started:
                vec = self._embedder.embed_document(
                    topic=line[2]["topic"], summary=line[2]["summary"]
                )
                blobs[line[0]] = vec.values.astype("<f4", copy=False).tobytes()

        with self._factory.connect() as con:
            raw = con.connection

            def _tx() -> tuple[int, int]:
                raw.execute("BEGIN IMMEDIATE")
                try:
                    deltas = self._write_batch(
                        envelope, started, memory_lines, audit_lines, blobs, raw, end
                    )
                    raw.commit()
                    return deltas
                except Exception:
                    raw.rollback()
                    raise

            try:
                delta_imported, delta_skipped = busy_retry(_tx)  # type: ignore[misc]
            except JsonlImportConflictError as exc:
                self._process_conflict(exc)
        self._imported += delta_imported
        self._skipped += delta_skipped
        if self._after_batch_commit is not None:
            self._after_batch_commit(end)

    def _process_conflict(self, exc: JsonlImportConflictError) -> None:
        """Mark the run failed once the batch lock is released, then re-raise."""
        self._mark_failed()
        raise exc

    def _write_batch(
        self,
        envelope: Envelope,
        started: int,
        memory_lines: list,
        audit_lines: list,
        blobs: dict[int, bytes],
        raw: sqlite3.Connection,
        end: int,
    ) -> tuple[int, int]:
        memory_insert = self._memory_insert_sql()
        audit_insert = self._audit_insert_sql()
        existing_mem = self._memories_existing(raw)
        existing_aud = self._audit_existing(raw)
        seen_mem: dict[tuple[str, str], dict] = {}
        seen_aud: dict[str, dict] = {}
        delta_imported = 0
        delta_skipped = 0
        for seq, kind, payload in memory_lines:
            key = (payload["brain_id"], payload["memory_id"])
            if payload["expires_at_ms"] <= started:
                continue  # counted as skipped up front, never written
            found = existing_mem.get(key)
            if found is None:
                found = seen_mem.get(key)
            if found is None:
                raw.execute(
                    memory_insert, self._memory_values(payload, blobs[seq])
                )
                seen_mem[key] = {
                    **payload, "metadata": _repo_json(payload["metadata"])
                }
                delta_imported += 1
                continue
            same = self._same_fields(found, payload)
            if same:
                delta_skipped += 1
                continue
            raise JsonlImportConflictError(
                f"memory {key[1]!r} for brain {key[0]!r} exists with differing"
                " preserved fields (idempotency key "
                f"memory:{key[0]}:{key[1]})"
            )
        for seq, kind, payload in audit_lines:
            event_id = payload["event_id"]
            found = existing_aud.get(event_id)
            if found is None:
                found = seen_aud.get(event_id)
            if found is None:
                raw.execute(audit_insert, self._audit_values(payload))
                seen_aud[event_id] = {
                    **payload, "detail": _repo_json(payload["detail"])
                }
                delta_imported += 1
                continue
            same = (
                found["action"] == payload["action"]
                and found["event_at_ms"] == payload["event_at_ms"]
                and found["detail"] == _repo_json(payload["detail"])
            )
            if same:
                delta_skipped += 1
                continue
            raise JsonlImportConflictError(
                f"audit event {event_id!r} exists with differing fields"
            )
        imported_total = self._imported + delta_imported
        skipped_total = self._skipped + delta_skipped
        raw.execute(
            "UPDATE import_runs SET last_committed_seq = ?,"
            " imported_count = ?, skipped_count = ? WHERE export_id = ?",
            (end, imported_total, skipped_total, envelope.export_id),
        )
        return delta_imported, delta_skipped

    def _complete_run(self, envelope: Envelope) -> None:
        with self._factory.connect() as con:
            raw = con.connection

            def _tx() -> None:
                raw.execute("BEGIN IMMEDIATE")
                try:
                    raw.execute(
                        "UPDATE import_runs SET status = 'completed',"
                        " completed_at_ms = ? WHERE export_id = ?",
                        (self._clock(), envelope.export_id),
                    )
                    raw.commit()
                except Exception:
                    raw.rollback()
                    raise

            busy_retry(_tx)
