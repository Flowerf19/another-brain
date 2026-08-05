"""JSONL v1 envelope parsing/validation (TASK-071, contract
.agents/contracts/another-brain-jsonl-v1.md). Import orchestration lands
with TASK-072.
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
