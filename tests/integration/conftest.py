"""Shared fixtures for the JSONL v1 importer integration tests (TASK-072).

Deterministic by construction: ``FakeClock`` everywhere, zero wall-clock
dependencies, and a real temp SQLite database built exactly like the unit
``sql_factory`` (bootstrap + migrate + the locked ``q4`` profile row).
``register_profile`` (called by the importer) is idempotent against that
row: it re-reads the profile and asserts ``(profile_id, model_revision,
input_version, dimension)`` matches the manifest (services/sql/profile.py),
which the unit fixture row satisfies, so no re-insert happens and the
manifest row stays the registered one.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.migrations import migrate
from tests.unit.conftest import FakeClock, FakeEmbedder

EXPORTED_AT_MS = 1_785_000_000_000
EXPORT_ID = "01234567-89ab-cdef-0123-456789abcdef"


@pytest.fixture
def import_factory(tmp_path) -> SQLiteConnectionFactory:
    """Bootstrapped, migrated v1 database; the importer registers the q4 row.

    ``register_profile`` (called by the importer) rejects any pre-seeded row
    whose ``model_revision`` differs from the manifest (services/sql/profile.py),
    so no PROFILE_SQL row is seeded here — the manifest row is the registered
    one, exactly as production opens the database.
    """
    factory = SQLiteConnectionFactory(tmp_path / "brain.sqlite3")
    factory.bootstrap()
    migrate(factory.db_path)
    return factory


def canonical(obj: object) -> str:
    """Canonical JSON text exactly as the contract (and repositories) use."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def memory_payload(
    memory_id: str,
    *,
    brain_id: str = "b1",
    agent_id: str = "agent-x",
    topic: str = "topic",
    catalog: str = "note",
    summary: str = "summary",
    content: str = "content",
    timeline_day: str = "2026-07-30",
    created_at_ms: int | None = None,
    updated_at_ms: int | None = None,
    expires_at_ms: int | None = None,
    exported_at_ms: int = EXPORTED_AT_MS,
    importance: int = 3,
    period_start_ms: int | None = None,
    period_end_ms: int | None = None,
    deleted_at_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
    record_version: int = 1,
) -> dict[str, Any]:
    """A valid memory payload with exactly the 18 contract keys."""
    created_at_ms = created_at_ms if created_at_ms is not None else exported_at_ms - 10_000_000
    updated_at_ms = updated_at_ms if updated_at_ms is not None else created_at_ms
    expires_at_ms = (
        expires_at_ms if expires_at_ms is not None else exported_at_ms + 86_400_000
    )
    return {
        "memory_id": memory_id,
        "brain_id": brain_id,
        "agent_id": agent_id,
        "topic": topic,
        "catalog": catalog,
        "summary": summary,
        "content": content,
        "timeline_day": timeline_day,
        "period_start_ms": period_start_ms,
        "period_end_ms": period_end_ms,
        "created_at_ms": created_at_ms,
        "updated_at_ms": updated_at_ms,
        "importance": importance,
        "expires_at_ms": expires_at_ms,
        "deleted_at_ms": deleted_at_ms,
        "metadata": metadata if metadata is not None else {},
        "record_version": record_version,
        "remaining_ttl_ms": max(0, expires_at_ms - exported_at_ms),
    }


def audit_payload(
    event_id: str,
    memory_id: str,
    *,
    brain_id: str = "b1",
    agent_id: str = "agent-x",
    action: str = "remember",
    event_at_ms: int | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A valid audit payload with exactly the 7 contract keys."""
    return {
        "event_id": event_id,
        "brain_id": brain_id,
        "memory_id": memory_id,
        "agent_id": agent_id,
        "action": action,
        "event_at_ms": (
            event_at_ms if event_at_ms is not None else EXPORTED_AT_MS - 1_000_000
        ),
        "detail": detail if detail is not None else {},
    }


def _data_line(seq: int, kind: str, payload: dict[str, Any]) -> str:
    idempotency_key = (
        f"memory:{payload['brain_id']}:{payload['memory_id']}"
        if kind == "memory"
        else f"audit:{payload['brain_id']}:{payload['event_id']}"
    )
    return canonical(
        {
            "seq": seq,
            "kind": kind,
            "idempotency_key": idempotency_key,
            "payload": payload,
            "payload_sha256": hashlib.sha256(
                canonical(payload).encode("utf-8")
            ).hexdigest(),
        }
    )


def build_envelope(
    memory_payloads: list[dict[str, Any]],
    audit_payloads: list[dict[str, Any]],
    *,
    export_id: str = EXPORT_ID,
    exported_at_ms: int = EXPORTED_AT_MS,
    source_app_version: str = "0.10.3",
    source_schema_version: str = "legacy-hybrid-1",
    source_commit: str = "edc0e573a10bb8ea9148c9830cf19fe15f757972",
    source_embedding_profile: str = "harrier-oss-v1-270m-fp32",
) -> bytes:
    """Canonical JSONL v1 envelope bytes (manifest + data + trailer)."""
    manifest = {
        "kind": "manifest",
        "format": "another-brain-jsonl",
        "format_version": 1,
        "export_id": export_id,
        "source_app_version": source_app_version,
        "source_schema_version": source_schema_version,
        "source_commit": source_commit,
        "source_embedding_profile": source_embedding_profile,
        "exported_at_ms": exported_at_ms,
        "expiry_mode": "absolute_epoch_ms",
        "memory_count": len(memory_payloads),
        "audit_count": len(audit_payloads),
    }
    lines = [canonical(manifest)]
    for payload in memory_payloads:
        lines.append(_data_line(len(lines), "memory", payload))
    for payload in audit_payloads:
        lines.append(_data_line(len(lines), "audit", payload))
    rolling = hashlib.sha256()
    for line in lines:
        rolling.update(line.encode("utf-8") + b"\n")
    trailer = canonical(
        {
            "kind": "trailer",
            "memory_count": len(memory_payloads),
            "audit_count": len(audit_payloads),
            "last_seq": len(memory_payloads) + len(audit_payloads),
            "rolling_sha256": rolling.hexdigest(),
        }
    )
    return ("\n".join(lines + [trailer]) + "\n").encode("utf-8")


def export_builder(
    tmp_path: Path,
    memory_payloads: list[dict[str, Any]],
    audit_payloads: list[dict[str, Any]],
    *,
    export_id: str | None = None,
    name: str = "export.jsonl",
    **manifest_overrides: Any,
) -> Path:
    """Write a canonical JSONL v1 envelope to ``tmp_path`` and return its path."""
    export_id = export_id or str(uuid.uuid4())
    data = build_envelope(
        memory_payloads,
        audit_payloads,
        export_id=export_id,
        **manifest_overrides,
    )
    path = tmp_path / name
    path.write_bytes(data)
    return path
